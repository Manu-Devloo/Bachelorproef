"""Business logic for the CTFd container challenge plugin."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
import ipaddress
from typing import Any
from uuid import uuid4

from .backend import BackendError, ContainerBackend, PublishedPortRange
from .storage import SQLiteStore
from .time_utils import as_utc_iso, utc_now


class ValidationError(ValueError):
    pass


class NotFoundError(LookupError):
    pass


class CapacityError(RuntimeError):
    pass


class BackendUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountContext:
    account_id: str
    account_type: str
    label: str
    user_id: int | None
    team_id: int | None


@dataclass(frozen=True)
class StartInstanceResult:
    instance: dict[str, Any]
    created: bool


class RuntimeService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        backend: ContainerBackend,
        public_host: str,
        public_scheme: str = "http",
        published_port_range: PublishedPortRange | None = None,
    ) -> None:
        self.store = store
        self.backend = backend
        self.public_host = public_host
        self.public_scheme = public_scheme
        self.published_port_range = published_port_range

    def start_instance(self, challenge: Any, account: AccountContext) -> StartInstanceResult:
        challenge_id = int(challenge.id)
        with self.store.locked():
            existing = self.store.get_running_instance(
                challenge_id=challenge_id,
                account_id=account.account_id,
            )
            if existing:
                return StartInstanceResult(instance=self._enrich_instance(existing), created=False)

            running = self.store.count_running_instances_for_challenge(challenge_id)
            if running >= int(challenge.max_instances):
                raise CapacityError(
                    f"Challenge '{challenge_id}' reached max_instances={challenge.max_instances}"
                )

            instance_id = uuid4().hex
            instance_name = self._build_container_name(challenge_id, account.label, instance_id)
            network_name = f"ctfdcnet-{instance_id[:12]}"
            labels = {
                "ctfd.plugin": "ctfd_container_challenges",
                "ctfd.challenge_id": str(challenge_id),
                "ctfd.account_id": account.account_id,
                "ctfd.instance_id": instance_id,
            }

            try:
                started = self.backend.start(
                    instance_name=instance_name,
                    image=str(challenge.image),
                    network_name=network_name,
                    container_port=int(challenge.container_port),
                    cpu_limit=float(challenge.cpu_limit),
                    memory_limit_mb=int(challenge.memory_limit_mb),
                    archive_path=getattr(challenge, "_archive_path", None),
                    labels=labels,
                    published_port_range=self.published_port_range,
                )
            except BackendError as exc:
                raise BackendUnavailableError(f"Failed to start challenge container: {exc}") from exc

            started_at = utc_now()
            expires_at = started_at + timedelta(seconds=int(challenge.timeout_seconds))
            created = self.store.create_instance(
                {
                    "instance_id": instance_id,
                    "challenge_id": challenge_id,
                    "account_id": account.account_id,
                    "account_type": account.account_type,
                    "user_id": account.user_id,
                    "team_id": account.team_id,
                    "container_id": started.container_id,
                    "network_name": network_name,
                    "host_port": started.host_port,
                    "status": "running",
                    "started_at": as_utc_iso(started_at),
                    "expires_at": as_utc_iso(expires_at),
                    "stopped_at": None,
                    "stop_reason": None,
                }
            )
            self.record_log(
                message="instance started",
                metadata={
                    "challenge_id": challenge_id,
                    "account_id": account.account_id,
                    "instance_id": instance_id,
                    "host_port": started.host_port,
                },
            )
            return StartInstanceResult(instance=self._enrich_instance(created), created=True)

    def get_active_instance(self, *, challenge_id: int, account_id: str) -> dict[str, Any] | None:
        instance = self.store.get_running_instance(
            challenge_id=challenge_id,
            account_id=account_id,
        )
        return self._enrich_instance(instance) if instance else None

    def stop_instance(self, instance_id: str, *, reason: str = "manual") -> dict[str, Any]:
        instance = self.store.get_instance(instance_id)
        if not instance:
            raise NotFoundError(f"Unknown instance '{instance_id}'")

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
            instance_id=instance_id,
            stop_reason=reason,
            stopped_at=as_utc_iso(utc_now()),
            final_status=final_status,
        )
        if not updated:
            raise NotFoundError(f"Unknown instance '{instance_id}'")

        self.record_log(
            message="instance stopped",
            metadata={
                "instance_id": instance_id,
                "challenge_id": updated["challenge_id"],
                "account_id": updated["account_id"],
                "reason": reason,
                "status": updated["status"],
            },
        )
        return self._enrich_instance(updated)

    def stop_active_instance(
        self,
        *,
        challenge_id: int,
        account_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        existing = self.store.get_running_instance(
            challenge_id=challenge_id,
            account_id=account_id,
        )
        if not existing:
            return None
        return self.stop_instance(existing["instance_id"], reason=reason)

    def stop_all_for_challenge(self, *, challenge_id: int, reason: str) -> list[dict[str, Any]]:
        running = self.store.list_instances(status="running", challenge_id=challenge_id)
        return [self.stop_instance(item["instance_id"], reason=reason) for item in running]

    def list_instances(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return [self._enrich_instance(item) for item in self.store.list_instances(status=status)]

    def reap_expired_instances(self) -> list[dict[str, Any]]:
        expired = self.store.list_expired_running_instances(as_utc_iso(utc_now()))
        results: list[dict[str, Any]] = []
        for item in expired:
            results.append(self.stop_instance(item["instance_id"], reason="timeout"))
        return results

    def health(self) -> dict[str, Any]:
        stats = self.store.stats()
        return {
            "storage": {
                "instances": stats.instance_count,
                "running_instances": stats.running_count,
                "logs": stats.log_count,
            },
            "backend": self.backend.health(),
            "public_endpoint": {
                "scheme": self.public_scheme,
                "host": self.public_host,
                "published_port_range": (
                    {
                        "start": self.published_port_range.start,
                        "end": self.published_port_range.end,
                    }
                    if self.published_port_range is not None
                    else None
                ),
            },
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
        row = self.store.add_log(
            level=str(level).strip().lower() or "info",
            message=clean_message,
            metadata_json=(
                json.dumps(metadata, ensure_ascii=False, default=str)
                if metadata is not None
                else None
            ),
            created_at=as_utc_iso(utc_now()),
        )
        return self._decode_log_row(row)

    def list_logs(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return [self._decode_log_row(row) for row in self.store.list_logs(limit=limit)]

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

    def _enrich_instance(self, instance: dict[str, Any] | None) -> dict[str, Any]:
        if not instance:
            raise NotFoundError("instance not found")
        enriched = dict(instance)
        if enriched.get("status") == "running":
            host = self._format_public_host(self.public_host)
            enriched["access_url"] = f"{self.public_scheme}://{host}:{enriched['host_port']}"
        else:
            enriched["access_url"] = None
        return enriched

    @staticmethod
    def _build_container_name(challenge_id: int, account_label: str, instance_id: str) -> str:
        safe_label = "".join(ch for ch in account_label.lower() if ch.isalnum()) or "acct"
        base = f"ctfdc-c{challenge_id}-{safe_label}-{instance_id[:8]}"
        return base[:63]

    @staticmethod
    def _format_public_host(public_host: str) -> str:
        host = str(public_host or "").strip()
        if not host:
            raise ValidationError("CTFD_CONTAINER_PUBLIC_HOST cannot be empty")
        if host.startswith("[") and host.endswith("]"):
            return host
        try:
            parsed = ipaddress.ip_address(host)
        except ValueError:
            return host
        if parsed.version == 6:
            return f"[{host}]"
        return host
