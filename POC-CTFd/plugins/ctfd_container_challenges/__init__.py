from __future__ import annotations

import atexit
from dataclasses import dataclass
import logging
import os
from threading import Lock
from typing import Any
from uuid import uuid4

from docker.tls import TLSConfig
from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from CTFd.models import Challenges, db
from CTFd.plugins import (
    register_admin_plugin_menu_bar,
    register_plugin_assets_directory,
    register_plugin_stylesheet,
)
from CTFd.plugins.challenges import CHALLENGE_CLASSES, BaseChallenge
from CTFd.plugins.migrations import upgrade
from CTFd.utils.config import is_teams_mode
from CTFd.utils.decorators import admins_only, authed_only, during_ctf_time_only, require_verified_emails
from CTFd.utils.user import get_current_team, get_current_user

from .backend import BackendError, DockerBackend, InMemoryBackend, PublishedPortRange
from .reaper import ReaperThread
from .service import (
    AccountContext,
    BackendUnavailableError,
    CapacityError,
    NotFoundError,
    RuntimeService,
    ValidationError,
)
from .storage import SQLiteStore
from .time_utils import as_utc_iso, utc_now

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
LOGGER = logging.getLogger(__name__)

PLUGIN_NAME = "ctfd_container_challenges"
ASSET_BASE = "/plugins/ctfd_container_challenges/assets/"
ALLOWED_ARCHIVE_EXTENSIONS = (".tar", ".tar.gz", ".tgz")


def _env_flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_optional(value: str | None) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _env_int(value: str | None, default: int, *, minimum: int | None = None) -> int:
    try:
        parsed = int(str(value or "").strip() or str(default))
    except ValueError:
        parsed = default
    if minimum is not None:
        return max(parsed, minimum)
    return parsed


def asset_url(filename: str) -> str:
    asset_path = os.path.join(os.path.dirname(__file__), "assets", filename)
    try:
        version = int(os.path.getmtime(asset_path))
    except OSError:
        version = 0
    return f"{ASSET_BASE}{filename}?v={version}"


class ContainerizedChallengeModel(Challenges):
    __mapper_args__ = {"polymorphic_identity": "containerized"}
    id = db.Column(
        db.Integer,
        db.ForeignKey("challenges.id", ondelete="CASCADE"),
        primary_key=True,
    )
    image = db.Column(db.String(255), nullable=False)
    container_port = db.Column(db.Integer, nullable=False, default=8080)
    cpu_limit = db.Column(db.Float, nullable=False, default=0.5)
    memory_limit_mb = db.Column(db.Integer, nullable=False, default=256)
    timeout_seconds = db.Column(db.Integer, nullable=False, default=900)
    max_instances = db.Column(db.Integer, nullable=False, default=30)


@dataclass(frozen=True)
class RuntimeConfig:
    backend: str
    store_path: str
    public_host: str
    public_scheme: str
    reaper_interval_seconds: float
    docker_host: str | None
    docker_tls_verify: bool
    docker_cert_path: str | None
    docker_timeout_seconds: int
    published_port_range: PublishedPortRange | None

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        published_port_min = _env_optional(os.getenv("CTFD_CONTAINER_PUBLISHED_PORT_MIN"))
        published_port_max = _env_optional(os.getenv("CTFD_CONTAINER_PUBLISHED_PORT_MAX"))
        published_port_range = None
        if published_port_min is not None or published_port_max is not None:
            try:
                start = int(published_port_min or "0")
                end = int(published_port_max or "0")
            except ValueError as exc:
                raise ValueError(
                    "CTFD_CONTAINER_PUBLISHED_PORT_MIN and "
                    "CTFD_CONTAINER_PUBLISHED_PORT_MAX must be integers"
                ) from exc
            if start < 1 or end < start or end > 65535:
                raise ValueError(
                    "Configured published port range must satisfy 1 <= min <= max <= 65535"
                )
            published_port_range = PublishedPortRange(start=start, end=end)

        return cls(
            backend=os.getenv("CTFD_CONTAINER_BACKEND", "docker").strip().lower(),
            store_path=os.getenv(
                "CTFD_CONTAINER_DB_PATH",
                "/var/ctfd-container/runtime.db",
            ).strip(),
            public_host=os.getenv("CTFD_CONTAINER_PUBLIC_HOST", "127.0.0.1").strip(),
            public_scheme=os.getenv("CTFD_CONTAINER_PUBLIC_SCHEME", "http").strip() or "http",
            reaper_interval_seconds=max(
                float(os.getenv("CTFD_CONTAINER_REAPER_INTERVAL", "5") or "5"),
                1.0,
            ),
            docker_host=_env_optional(
                os.getenv("CTFD_CONTAINER_DOCKER_HOST") or os.getenv("DOCKER_HOST")
            ),
            docker_tls_verify=_env_flag(
                os.getenv("CTFD_CONTAINER_DOCKER_TLS_VERIFY")
                if os.getenv("CTFD_CONTAINER_DOCKER_TLS_VERIFY") is not None
                else os.getenv("DOCKER_TLS_VERIFY")
            ),
            docker_cert_path=_env_optional(
                os.getenv("CTFD_CONTAINER_DOCKER_CERT_PATH") or os.getenv("DOCKER_CERT_PATH")
            ),
            docker_timeout_seconds=_env_int(
                os.getenv("CTFD_CONTAINER_DOCKER_TIMEOUT"),
                60,
                minimum=5,
            ),
            published_port_range=published_port_range,
        )

    @property
    def use_mock_backend(self) -> bool:
        return self.backend == "mock"


class RuntimeManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._started = False
        self._service: RuntimeService | None = None
        self._store: SQLiteStore | None = None
        self._reaper: ReaperThread | None = None

    def ensure_started(self) -> RuntimeService:
        with self._lock:
            if self._started and self._service is not None:
                return self._service

            cfg = RuntimeConfig.from_env()
            os.makedirs(os.path.dirname(cfg.store_path), exist_ok=True)
            store = SQLiteStore(cfg.store_path)
            backend = (
                InMemoryBackend()
                if cfg.use_mock_backend
                else DockerBackend(
                    base_url=cfg.docker_host,
                    tls_config=_build_docker_tls_config(cfg),
                    timeout_seconds=cfg.docker_timeout_seconds,
                )
            )
            service = RuntimeService(
                store=store,
                backend=backend,
                public_host=cfg.public_host,
                public_scheme=cfg.public_scheme,
                published_port_range=cfg.published_port_range,
            )
            reaper = ReaperThread(service=service, interval_seconds=cfg.reaper_interval_seconds)
            reaper.start()

            self._service = service
            self._store = store
            self._reaper = reaper
            self._started = True
            return service

    def shutdown(self) -> None:
        with self._lock:
            if self._reaper is not None:
                self._reaper.shutdown()
            if self._store is not None:
                self._store.close()
            self._service = None
            self._store = None
            self._reaper = None
            self._started = False


RUNTIME_MANAGER = RuntimeManager()


def _build_docker_tls_config(cfg: RuntimeConfig) -> TLSConfig | bool | None:
    if not cfg.docker_host:
        return None

    cert_path = cfg.docker_cert_path
    tls_enabled = cfg.docker_tls_verify or cert_path is not None
    if not tls_enabled:
        return None

    if cert_path is None:
        return cfg.docker_tls_verify

    ca_cert = os.path.join(cert_path, "ca.pem")
    client_cert = os.path.join(cert_path, "cert.pem")
    client_key = os.path.join(cert_path, "key.pem")

    tls_kwargs: dict[str, Any] = {"verify": cfg.docker_tls_verify}
    if os.path.exists(ca_cert):
        tls_kwargs["ca_cert"] = ca_cert
    if os.path.exists(client_cert) and os.path.exists(client_key):
        tls_kwargs["client_cert"] = (client_cert, client_key)
    return TLSConfig(**tls_kwargs)


def get_runtime_service() -> RuntimeService:
    return RUNTIME_MANAGER.ensure_started()


def build_account_context_for(user: Any, team: Any | None = None) -> AccountContext:
    if user is None:
        raise ValidationError("Authenticated user is required")

    if is_teams_mode():
        if team is None:
            raise ValidationError("A team is required in teams mode")
        return AccountContext(
            account_id=f"team-{team.id}",
            account_type="team",
            label=f"team{team.id}",
            user_id=user.id,
            team_id=team.id,
        )

    return AccountContext(
        account_id=f"user-{user.id}",
        account_type="user",
        label=f"user{user.id}",
        user_id=user.id,
        team_id=None,
    )


def build_account_context() -> AccountContext:
    return build_account_context_for(get_current_user(), get_current_team())


def archive_storage_dir() -> str:
    path = (
        os.getenv("CTFD_CONTAINER_IMAGE_ARCHIVE_DIR", "/var/ctfd-container/images").strip()
        or "/var/ctfd-container/images"
    )
    os.makedirs(path, exist_ok=True)
    return path


def _normalize_archive_name(filename: str) -> str:
    clean = secure_filename(filename or "image.tar")
    return clean or "image.tar"


def _build_uploaded_image_tag(filename: str, token: str) -> str:
    stem = filename.lower()
    for suffix in ALLOWED_ARCHIVE_EXTENSIONS:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    stem = "".join(ch if ch.isalnum() else "-" for ch in stem).strip("-") or "uploaded-image"
    return f"ctfd-upload/{stem}:{token[:12]}"


def _resolve_image_asset(asset_token: str) -> dict[str, Any]:
    asset = get_runtime_service().store.get_image_asset(asset_token)
    if not asset:
        raise ValidationError(f"Unknown uploaded image token '{asset_token}'")
    return asset


def _resolve_image_asset_by_image_tag(image_tag: str) -> dict[str, Any] | None:
    tag = str(image_tag or "").strip()
    if not tag.startswith("ctfd-upload/"):
        return None
    return get_runtime_service().store.get_image_asset_by_image_tag(tag)


def _get_bound_image_asset(challenge_id: int) -> dict[str, Any] | None:
    return get_runtime_service().store.get_image_asset_for_challenge(challenge_id)


def _bind_image_asset(challenge_id: int, asset_token: str) -> dict[str, Any]:
    asset = _resolve_image_asset(asset_token)
    if not os.path.exists(asset["archive_path"]):
        raise ValidationError(f"Uploaded image archive is missing for token '{asset_token}'")
    bound = get_runtime_service().store.bind_image_asset(
        asset_token=asset_token,
        challenge_id=challenge_id,
        updated_at=as_utc_iso(utc_now()),
    )
    if not bound:
        raise ValidationError(f"Failed to bind uploaded image token '{asset_token}'")
    return bound


def _bind_image_asset_for_challenge(
    challenge_id: int,
    *,
    asset_token: str | None,
    image_tag: str,
) -> dict[str, Any] | None:
    if asset_token:
        return _bind_image_asset(challenge_id, asset_token)

    asset = _resolve_image_asset_by_image_tag(image_tag)
    if not asset:
        return None
    if not os.path.exists(asset["archive_path"]):
        raise ValidationError(f"Uploaded image archive is missing for image '{image_tag}'")
    bound = get_runtime_service().store.bind_image_asset(
        asset_token=asset["asset_token"],
        challenge_id=challenge_id,
        updated_at=as_utc_iso(utc_now()),
    )
    if not bound:
        raise ValidationError(f"Failed to bind uploaded image for challenge '{challenge_id}'")
    return bound


def _delete_bound_image_asset(challenge_id: int) -> None:
    asset = _get_bound_image_asset(challenge_id)
    if not asset:
        return
    deleted = get_runtime_service().store.delete_image_asset(asset["asset_token"])
    if deleted and os.path.exists(deleted["archive_path"]):
        try:
            os.remove(deleted["archive_path"])
        except OSError:
            LOGGER.exception("failed to delete uploaded image archive")


def _store_uploaded_archive(uploaded_file: Any) -> dict[str, Any]:
    filename = _normalize_archive_name(getattr(uploaded_file, "filename", ""))
    lowered = filename.lower()
    if not any(lowered.endswith(ext) for ext in ALLOWED_ARCHIVE_EXTENSIONS):
        raise ValidationError("Docker image archive must be a .tar, .tar.gz, or .tgz file")

    token = uuid4().hex
    archive_path = os.path.join(archive_storage_dir(), f"{token}-{filename}")
    image_tag = _build_uploaded_image_tag(filename, token)
    uploaded_file.save(archive_path)

    try:
        service = get_runtime_service()
        service.backend.import_image_archive(archive_path=archive_path, image_tag=image_tag)
        timestamp = as_utc_iso(utc_now())
        return service.store.upsert_image_asset(
            asset_token=token,
            original_filename=filename,
            archive_path=archive_path,
            image_tag=image_tag,
            created_at=timestamp,
            updated_at=timestamp,
        )
    except BackendError as exc:
        if os.path.exists(archive_path):
            try:
                os.remove(archive_path)
            except OSError:
                LOGGER.exception("failed to cleanup uploaded archive after import error")
        raise BackendUnavailableError(f"Failed to import Docker archive: {exc}") from exc
    except Exception:
        if os.path.exists(archive_path):
            try:
                os.remove(archive_path)
            except OSError:
                LOGGER.exception("failed to cleanup uploaded archive after import error")
        raise


def _safe_record_log(service: RuntimeService, *, message: str, metadata: dict[str, Any] | None = None) -> None:
    try:
        service.record_log(message=message, metadata=metadata)
    except Exception:
        LOGGER.exception("failed to write runtime log")


class ContainerizedChallenge(BaseChallenge):
    id = "containerized"
    name = "containerized"
    templates = {
        "create": "/plugins/ctfd_container_challenges/assets/create.html",
        "update": "/plugins/ctfd_container_challenges/assets/update.html",
        "view": "/plugins/ctfd_container_challenges/assets/view.html",
    }
    scripts = {
        "create": asset_url("create.js"),
        "update": f"{ASSET_BASE}update.js",
        "view": asset_url("view.js"),
    }
    route = ASSET_BASE
    blueprint = Blueprint(
        "ctfd_container_challenges",
        __name__,
        template_folder="templates",
        static_folder="assets",
    )
    challenge_model = ContainerizedChallengeModel

    @classmethod
    def create(cls, request):
        data = dict(request.form or request.get_json() or {})
        uploaded_image_token = str(data.pop("uploaded_image_token", "")).strip() or None
        if uploaded_image_token:
            data["image"] = _resolve_image_asset(uploaded_image_token)["image_tag"]
        sanitized = cls._sanitize_payload(data)
        challenge = cls.challenge_model(**sanitized)
        db.session.add(challenge)
        db.session.commit()
        _bind_image_asset_for_challenge(
            challenge.id,
            asset_token=uploaded_image_token,
            image_tag=challenge.image,
        )
        return challenge

    @classmethod
    def read(cls, challenge):
        challenge = cls.challenge_model.query.filter_by(id=challenge.id).first()
        data = super().read(challenge)
        image_asset = _get_bound_image_asset(challenge.id)
        using_uploaded_archive = bool(image_asset and image_asset["image_tag"] == challenge.image)
        data.update(
            {
                "image": challenge.image,
                "container_port": challenge.container_port,
                "cpu_limit": challenge.cpu_limit,
                "memory_limit_mb": challenge.memory_limit_mb,
                "timeout_seconds": challenge.timeout_seconds,
                "max_instances": challenge.max_instances,
                "image_source": "archive" if using_uploaded_archive else "registry",
                "image_archive_name": image_asset["original_filename"] if using_uploaded_archive else "",
            }
        )
        return data

    @classmethod
    def update(cls, challenge, request):
        data = dict(request.form or request.get_json() or {})
        uploaded_image_token = str(data.pop("uploaded_image_token", "")).strip() or None
        if uploaded_image_token:
            data["image"] = _resolve_image_asset(uploaded_image_token)["image_tag"]
        sanitized = cls._sanitize_payload(data, partial=True)
        for attr, value in sanitized.items():
            setattr(challenge, attr, value)
        db.session.commit()
        _bind_image_asset_for_challenge(
            challenge.id,
            asset_token=uploaded_image_token,
            image_tag=challenge.image,
        )
        return challenge

    @classmethod
    def delete(cls, challenge):
        try:
            service = get_runtime_service()
            service.stop_all_for_challenge(challenge_id=challenge.id, reason="challenge-deleted")
        except Exception:
            LOGGER.exception("failed to stop runtime instances before challenge delete")
        try:
            _delete_bound_image_asset(challenge.id)
        except Exception:
            LOGGER.exception("failed to cleanup uploaded archive during challenge delete")
        super().delete(challenge)

    @classmethod
    def attempt(cls, challenge, request):
        response = super().attempt(challenge, request)

        if request.args.get("preview"):
            return response

        status = response[0] if isinstance(response, tuple) else response.status
        if status == "correct" or status is True:
            try:
                account = build_account_context()
                get_runtime_service().stop_active_instance(
                    challenge_id=challenge.id,
                    account_id=account.account_id,
                    reason="challenge-solved",
                )
            except Exception:
                LOGGER.exception("failed to stop runtime instance after correct attempt")

        return response

    @classmethod
    def solve(cls, user, team, challenge, request):
        super().solve(user, team, challenge, request)
        try:
            account = build_account_context_for(user, team)
            get_runtime_service().stop_active_instance(
                challenge_id=challenge.id,
                account_id=account.account_id,
                reason="challenge-solved",
            )
        except Exception:
            LOGGER.exception("failed to stop runtime instance after solve")

    @staticmethod
    def _sanitize_payload(data: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
        payload = dict(data)
        payload["type"] = "containerized"
        if not payload.get("function"):
            payload["function"] = "static"

        def require_string(field: str) -> str:
            value = str(payload.get(field, "")).strip()
            if not value and not partial:
                raise ValidationError(f"{field} is required")
            if value:
                payload[field] = value
            elif field in payload and not value:
                raise ValidationError(f"{field} is required")
            return value

        def coerce_int(field: str, lower: int, upper: int) -> None:
            if field not in payload and partial:
                return
            try:
                value = int(payload.get(field))
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"{field} must be an integer") from exc
            if value < lower or value > upper:
                raise ValidationError(f"{field} must be between {lower} and {upper}")
            payload[field] = value

        def coerce_float(field: str, lower: float, upper: float) -> None:
            if field not in payload and partial:
                return
            try:
                value = float(payload.get(field))
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"{field} must be a number") from exc
            if value < lower or value > upper:
                raise ValidationError(f"{field} must be between {lower} and {upper}")
            payload[field] = round(value, 2)

        require_string("image")
        coerce_int("container_port", 1, 65535)
        coerce_float("cpu_limit", 0.1, 8.0)
        coerce_int("memory_limit_mb", 64, 16384)
        coerce_int("timeout_seconds", 30, 86400)
        coerce_int("max_instances", 1, 2000)
        return payload


plugin_blueprint = Blueprint(
    "ctfd_container_challenges_api",
    __name__,
    template_folder="templates",
)


def _json_error(message: str, code: int) -> tuple[Response, int]:
    return jsonify({"success": False, "error": message}), code


def _build_admin_dashboard(service: RuntimeService) -> dict[str, Any]:
    challenges = ContainerizedChallengeModel.query.order_by(
        ContainerizedChallengeModel.category.asc(),
        ContainerizedChallengeModel.name.asc(),
    ).all()
    challenge_index = {challenge.id: challenge for challenge in challenges}

    running_counts: dict[int, int] = {}
    for item in service.list_instances(status="running"):
        challenge_id = int(item["challenge_id"])
        running_counts[challenge_id] = running_counts.get(challenge_id, 0) + 1

    challenge_rows = [
        {
            "id": challenge.id,
            "name": challenge.name,
            "category": challenge.category,
            "image": challenge.image,
            "image_archive_name": (
                _get_bound_image_asset(challenge.id) or {}
            ).get("original_filename", ""),
            "container_port": challenge.container_port,
            "cpu_limit": challenge.cpu_limit,
            "memory_limit_mb": challenge.memory_limit_mb,
            "timeout_seconds": challenge.timeout_seconds,
            "max_instances": challenge.max_instances,
            "running_instances": running_counts.get(challenge.id, 0),
        }
        for challenge in challenges
    ]

    instance_rows: list[dict[str, Any]] = []
    for item in service.list_instances():
        challenge = challenge_index.get(int(item["challenge_id"]))
        row = dict(item)
        row["challenge_name"] = challenge.name if challenge else f"Challenge {item['challenge_id']}"
        row["challenge_category"] = challenge.category if challenge else ""
        row["image"] = challenge.image if challenge else ""
        row["cpu_limit"] = challenge.cpu_limit if challenge else None
        row["memory_limit_mb"] = challenge.memory_limit_mb if challenge else None
        row["timeout_seconds"] = challenge.timeout_seconds if challenge else None
        row["max_instances"] = challenge.max_instances if challenge else None
        instance_rows.append(row)

    return {
        "instances": instance_rows,
        "challenges": challenge_rows,
        "logs": service.list_logs(limit=100),
        "health": service.health(),
    }


@plugin_blueprint.get("/plugins/ctfd_container_challenges/api/challenges/<int:challenge_id>/instance")
@authed_only
@during_ctf_time_only
@require_verified_emails
def get_instance(challenge_id: int):
    service = get_runtime_service()
    try:
        account = build_account_context()
    except ValidationError as exc:
        return _json_error(str(exc), 400)
    instance = service.get_active_instance(challenge_id=challenge_id, account_id=account.account_id)
    return jsonify({"success": True, "instance": instance})


@plugin_blueprint.post("/plugins/ctfd_container_challenges/api/challenges/<int:challenge_id>/instance")
@authed_only
@during_ctf_time_only
@require_verified_emails
def start_instance(challenge_id: int):
    service = get_runtime_service()
    challenge = ContainerizedChallengeModel.query.filter_by(id=challenge_id).first()
    if not challenge:
        return _json_error("Unknown challenge", 404)
    image_asset = _get_bound_image_asset(challenge_id)
    if image_asset and image_asset["image_tag"] == challenge.image:
        challenge._archive_path = image_asset["archive_path"]

    try:
        account = build_account_context()
    except ValidationError as exc:
        return _json_error(str(exc), 400)
    try:
        result = service.start_instance(challenge, account)
    except ValidationError as exc:
        return _json_error(str(exc), 400)
    except CapacityError as exc:
        return _json_error(str(exc), 409)
    except BackendUnavailableError as exc:
        return _json_error(str(exc), 503)

    _safe_record_log(
        service,
        message="instance start requested",
        metadata={
            "challenge_id": challenge_id,
            "account_id": account.account_id,
            "created": result.created,
            "instance_id": result.instance["instance_id"],
        },
    )
    return jsonify({"success": True, "created": result.created, "instance": result.instance}), (201 if result.created else 200)


@plugin_blueprint.delete("/plugins/ctfd_container_challenges/api/challenges/<int:challenge_id>/instance")
@authed_only
@during_ctf_time_only
@require_verified_emails
def stop_instance(challenge_id: int):
    service = get_runtime_service()
    try:
        account = build_account_context()
    except ValidationError as exc:
        return _json_error(str(exc), 400)
    instance = service.stop_active_instance(
        challenge_id=challenge_id,
        account_id=account.account_id,
        reason="manual",
    )
    return jsonify({"success": True, "instance": instance})


@plugin_blueprint.get("/plugins/ctfd_container_challenges/api/admin/instances")
@admins_only
def admin_instances():
    service = get_runtime_service()
    return jsonify({"success": True, "items": service.list_instances()})


@plugin_blueprint.get("/plugins/ctfd_container_challenges/api/admin/logs")
@admins_only
def admin_logs():
    service = get_runtime_service()
    return jsonify({"success": True, "items": service.list_logs(limit=200)})


@plugin_blueprint.post("/plugins/ctfd_container_challenges/api/admin/reaper/run")
@admins_only
def admin_run_reaper():
    service = get_runtime_service()
    expired = service.reap_expired_instances()
    return jsonify({"success": True, "expired": len(expired), "items": expired})


@plugin_blueprint.post("/plugins/ctfd_container_challenges/api/admin/images/upload")
@admins_only
def admin_upload_image_archive():
    uploaded_file = request.files.get("image_archive")
    if uploaded_file is None or not getattr(uploaded_file, "filename", ""):
        return _json_error("An image archive file is required", 400)

    try:
        asset = _store_uploaded_archive(uploaded_file)
    except ValidationError as exc:
        return _json_error(str(exc), 400)
    except BackendUnavailableError as exc:
        return _json_error(str(exc), 503)
    except Exception as exc:
        LOGGER.exception("failed to import uploaded image archive")
        return _json_error(f"Failed to import Docker archive: {exc}", 500)

    return jsonify(
        {
            "success": True,
            "asset": {
                "asset_token": asset["asset_token"],
                "image_tag": asset["image_tag"],
                "original_filename": asset["original_filename"],
            },
        }
    )


@plugin_blueprint.post("/admin/plugins/containerized-challenges/instances/<instance_id>/stop")
@admins_only
def admin_force_stop_instance(instance_id: str):
    service = get_runtime_service()
    existing = service.store.get_instance(instance_id)
    if not existing:
        flash(f"Unknown runtime instance '{instance_id}'.", "danger")
        return redirect(url_for("ctfd_container_challenges_api.admin_page"))

    if existing["status"] != "running":
        flash(
            f"Instance {instance_id[:12]} is already {existing['status']}.",
            "warning",
        )
        return redirect(url_for("ctfd_container_challenges_api.admin_page"))

    stopped = service.stop_instance(instance_id, reason="admin-force-stop")
    flash(
        f"Stopped instance {instance_id[:12]} for challenge {stopped['challenge_id']}.",
        "success",
    )
    return redirect(url_for("ctfd_container_challenges_api.admin_page"))


@plugin_blueprint.post("/admin/plugins/containerized-challenges/challenges/<int:challenge_id>/stop-all")
@admins_only
def admin_force_stop_challenge(challenge_id: int):
    service = get_runtime_service()
    challenge = ContainerizedChallengeModel.query.filter_by(id=challenge_id).first()
    if not challenge:
        flash(f"Unknown challenge '{challenge_id}'.", "danger")
        return redirect(url_for("ctfd_container_challenges_api.admin_page"))

    stopped = service.stop_all_for_challenge(challenge_id=challenge_id, reason="admin-force-stop")
    if stopped:
        flash(
            f"Stopped {len(stopped)} running instance(s) for '{challenge.name}'.",
            "success",
        )
    else:
        flash(f"No running instances for '{challenge.name}'.", "info")
    return redirect(url_for("ctfd_container_challenges_api.admin_page"))


@plugin_blueprint.post("/admin/plugins/containerized-challenges/reaper/run")
@admins_only
def admin_run_reaper_page():
    service = get_runtime_service()
    expired = service.reap_expired_instances()
    flash(f"Reaper stopped {len(expired)} expired instance(s).", "success")
    return redirect(url_for("ctfd_container_challenges_api.admin_page"))


@plugin_blueprint.get("/admin/plugins/containerized-challenges")
@admins_only
def admin_page():
    service = get_runtime_service()
    return render_template("admin.html", **_build_admin_dashboard(service))


def load(app):
    upgrade(plugin_name=PLUGIN_NAME)
    CHALLENGE_CLASSES["containerized"] = ContainerizedChallenge
    register_plugin_assets_directory(app, base_path=ASSET_BASE)
    register_plugin_stylesheet(asset_url("styles.css"))
    register_admin_plugin_menu_bar("Container Runtime", "/admin/plugins/containerized-challenges")
    app.register_blueprint(plugin_blueprint)

    @app.before_request
    def _ensure_runtime_started() -> None:
        if request.endpoint and request.endpoint.startswith("static"):
            return
        get_runtime_service()

    @atexit.register
    def _shutdown_runtime() -> None:
        RUNTIME_MANAGER.shutdown()
