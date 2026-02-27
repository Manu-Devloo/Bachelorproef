"""Business logic for challenge registration and instance lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
import re
from typing import Any
from uuid import uuid4

from .backend import BackendError, ContainerBackend
from .storage import SQLiteStore
from .time_utils import as_utc_iso, utc_now


class ValidationError(ValueError):
    pass


class NotFoundError(LookupError):
    pass


class CapacityError(RuntimeError):
    pass


@dataclass(frozen=True)
class StartInstanceResult:
    instance: dict[str, Any]
    created: bool


class OrchestratorService:
    def __init__(self, *, store: SQLiteStore, backend: ContainerBackend, public_host: str) -> None:
        self.store = store
        self.backend = backend
        self.public_host = public_host

    def register_challenge(self, payload: dict[str, Any]) -> dict[str, Any]:
        challenge_id = self._normalize_identifier(payload.get("challenge_id"), "challenge_id")
        name = self._require_str(payload.get("name"), "name")
        image = self._require_str(payload.get("image"), "image")
        container_port = self._bounded_int(payload.get("container_port"), "container_port", 1, 65535)
        cpu_limit = self._bounded_float(payload.get("cpu_limit", 0.5), "cpu_limit", 0.1, 8.0)
        memory_limit_mb = self._bounded_int(
            payload.get("memory_limit_mb", 256),
            "memory_limit_mb",
            64,
            16384,
        )
        timeout_seconds = self._bounded_int(
            payload.get("timeout_seconds", 900),
            "timeout_seconds",
            30,
            86400,
        )
        max_instances = self._bounded_int(
            payload.get("max_instances", 30),
            "max_instances",
            1,
            2000,
        )

        now = as_utc_iso(utc_now())
        challenge = {
            "challenge_id": challenge_id,
            "name": name,
            "image": image,
            "container_port": container_port,
            "cpu_limit": cpu_limit,
            "memory_limit_mb": memory_limit_mb,
            "timeout_seconds": timeout_seconds,
            "max_instances": max_instances,
            "created_at": now,
            "updated_at": now,
        }
        return self.store.upsert_challenge(challenge)

    def list_challenges(self) -> list[dict[str, Any]]:
        return self.store.list_challenges()

    def start_instance(self, payload: dict[str, Any]) -> StartInstanceResult:
        challenge_key = payload.get("challenge_id", payload.get("registry_id"))
        challenge_id = self._normalize_identifier(challenge_key, "challenge_id")
        user_id = self._normalize_identifier(payload.get("user_id"), "user_id")
        challenge = self.store.get_challenge(challenge_id)
        if not challenge:
            raise NotFoundError(f"Unknown challenge '{challenge_id}'")

        existing = self.store.get_running_instance(challenge_id=challenge_id, user_id=user_id)
        if existing:
            return StartInstanceResult(instance=self._enrich_instance(existing), created=False)

        running = self.store.count_running_instances_for_challenge(challenge_id)
        if running >= int(challenge["max_instances"]):
            raise CapacityError(
                f"Challenge '{challenge_id}' reached max_instances={challenge['max_instances']}"
            )

        instance_id = uuid4().hex
        instance_name = self._build_container_name(challenge_id, user_id, instance_id)
        network_name = f"pocnet-{instance_id[:12]}"
        labels = {
            "poc.managed": "true",
            "poc.challenge": challenge_id,
            "poc.user": user_id,
            "poc.instance": instance_id,
        }

        try:
            started = self.backend.start(
                instance_name=instance_name,
                image=challenge["image"],
                network_name=network_name,
                container_port=int(challenge["container_port"]),
                cpu_limit=float(challenge["cpu_limit"]),
                memory_limit_mb=int(challenge["memory_limit_mb"]),
                labels=labels,
            )
        except BackendError as exc:
            raise CapacityError(f"Failed to start challenge container: {exc}") from exc

        started_at = utc_now()
        expires_at = started_at + timedelta(seconds=int(challenge["timeout_seconds"]))
        record = {
            "instance_id": instance_id,
            "challenge_id": challenge_id,
            "user_id": user_id,
            "container_id": started.container_id,
            "network_name": network_name,
            "host_port": started.host_port,
            "status": "running",
            "started_at": as_utc_iso(started_at),
            "expires_at": as_utc_iso(expires_at),
            "stopped_at": None,
            "stop_reason": None,
        }
        created = self.store.create_instance(record)
        return StartInstanceResult(instance=self._enrich_instance(created), created=True)

    def stop_instance(self, instance_id: str, *, reason: str = "manual") -> dict[str, Any]:
        clean_instance_id = self._normalize_identifier(instance_id, "instance_id")
        instance = self.store.get_instance(clean_instance_id)
        if not instance:
            raise NotFoundError(f"Unknown instance '{clean_instance_id}'")

        if instance["status"] != "running":
            return self._enrich_instance(instance)

        final_status = "expired" if reason == "timeout" else "stopped"
        try:
            self.backend.stop(
                container_id=instance["container_id"],
                network_name=instance["network_name"],
            )
        except BackendError:
            final_status = "orphaned"

        updated = self.store.mark_instance_stopped(
            instance_id=clean_instance_id,
            stop_reason=reason,
            stopped_at=as_utc_iso(utc_now()),
            final_status=final_status,
        )
        if not updated:
            raise NotFoundError(f"Unknown instance '{clean_instance_id}'")
        return self._enrich_instance(updated)

    def list_instances(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return [self._enrich_instance(item) for item in self.store.list_instances(status=status)]

    def stop_all_running_instances(self, *, reason: str = "bulk-stop") -> list[dict[str, Any]]:
        running = self.store.list_instances(status="running")
        stopped: list[dict[str, Any]] = []
        for item in running:
            stopped.append(self.stop_instance(item["instance_id"], reason=reason))
        return stopped

    def reap_expired_instances(self) -> list[dict[str, Any]]:
        now_iso = as_utc_iso(utc_now())
        expired = self.store.list_expired_running_instances(now_iso)
        results: list[dict[str, Any]] = []
        for item in expired:
            results.append(self.stop_instance(item["instance_id"], reason="timeout"))
        return results

    def health(self) -> dict[str, Any]:
        stats = self.store.stats()
        return {
            "storage": {
                "challenges": stats.challenge_count,
                "instances": stats.instance_count,
            },
            "backend": self.backend.health(),
        }

    def record_log(
        self,
        *,
        message: str,
        metadata: Any | None = None,
        level: str = "info",
    ) -> dict[str, Any]:
        clean_message = str(message).strip()
        if not clean_message:
            raise ValidationError("message is required")
        clean_level = str(level).strip().lower() or "info"
        metadata_json = (
            json.dumps(metadata, ensure_ascii=False, default=str)
            if metadata is not None
            else None
        )
        row = self.store.add_log(
            level=clean_level,
            message=clean_message,
            metadata_json=metadata_json,
            created_at=as_utc_iso(utc_now()),
        )
        return self._decode_log_row(row)

    def list_logs(self, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.store.list_logs(limit=limit)
        return [self._decode_log_row(row) for row in rows]

    def clear_logs(self) -> int:
        return self.store.clear_logs()

    @staticmethod
    def _decode_log_row(row: dict[str, Any]) -> dict[str, Any]:
        decoded = dict(row)
        metadata_json = decoded.pop("metadata_json", None)
        if metadata_json:
            try:
                decoded["metadata"] = json.loads(metadata_json)
            except json.JSONDecodeError:
                decoded["metadata"] = metadata_json
        else:
            decoded["metadata"] = None
        return decoded

    def _enrich_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(instance)
        if enriched.get("status") == "running":
            enriched["access_url"] = f"http://{self.public_host}:{enriched['host_port']}"
        else:
            enriched["access_url"] = None
        return enriched

    @staticmethod
    def _require_str(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field} is required")
        return value.strip()

    @classmethod
    def _normalize_identifier(cls, value: Any, field: str) -> str:
        raw = cls._require_str(value, field).lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", raw):
            raise ValidationError(
                f"{field} must match [a-z0-9][a-z0-9-]{{1,62}}"
            )
        return raw

    @staticmethod
    def _bounded_int(value: Any, field: str, lower: int, upper: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{field} must be an integer") from exc
        if parsed < lower or parsed > upper:
            raise ValidationError(f"{field} must be between {lower} and {upper}")
        return parsed

    @staticmethod
    def _bounded_float(value: Any, field: str, lower: float, upper: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{field} must be a number") from exc
        if parsed < lower or parsed > upper:
            raise ValidationError(f"{field} must be between {lower} and {upper}")
        return round(parsed, 2)

    @staticmethod
    def _build_container_name(challenge_id: str, user_id: str, instance_id: str) -> str:
        base = f"poc-{challenge_id}-{user_id}-{instance_id[:8]}"
        return base[:63]
