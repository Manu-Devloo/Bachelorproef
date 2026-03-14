"""Flask API + dashboard for the challenge container orchestrator PoC."""

from __future__ import annotations

import atexit
import json
import logging
import os
from typing import Any

from flask import Flask, jsonify, render_template, request

from .backend import DockerBackend, InMemoryBackend
from .config import Settings
from .reaper import ReaperThread
from .service import (
    BackendUnavailableError,
    CapacityError,
    NotFoundError,
    OrchestratorService,
    ValidationError,
)
from .storage import SQLiteStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)


def _ensure_parent_dir(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _safe_record_log(
    service: OrchestratorService,
    *,
    message: str,
    metadata: dict[str, Any] | None = None,
    level: str = "info",
) -> None:
    try:
        service.record_log(message=message, metadata=metadata, level=level)
    except Exception:
        logging.exception("failed to persist log entry")


def _bootstrap_challenges(service: OrchestratorService, cfg: Settings) -> None:
    if not cfg.bootstrap_challenges_json:
        return

    try:
        parsed = json.loads(cfg.bootstrap_challenges_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("POC_BOOTSTRAP_CHALLENGES must contain valid JSON") from exc

    if isinstance(parsed, dict):
        payloads = [parsed]
    elif isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
        payloads = parsed
    else:
        raise RuntimeError(
            "POC_BOOTSTRAP_CHALLENGES must be a JSON object or array of objects"
        )

    for payload in payloads:
        challenge = service.register_challenge(payload)
        _safe_record_log(
            service,
            message="bootstrap registry item loaded",
            metadata={
                "challenge_id": challenge["challenge_id"],
                "image": challenge["image"],
            },
        )


def create_app(settings: Settings | None = None) -> Flask:
    cfg = settings or Settings.from_env()
    _ensure_parent_dir(cfg.db_path)

    store = SQLiteStore(cfg.db_path)
    backend = InMemoryBackend() if cfg.is_mock_backend else DockerBackend()
    service = OrchestratorService(
        store=store,
        backend=backend,
        public_host=cfg.default_public_host,
    )
    _bootstrap_challenges(service, cfg)
    reaper = ReaperThread(service=service, interval_seconds=cfg.reaper_interval)
    reaper.start()

    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.config["service"] = service
    app.config["settings"] = cfg
    app.config["store"] = store
    app.config["reaper"] = reaper

    @atexit.register
    def _shutdown() -> None:
        reaper.shutdown()
        store.close()

    @app.get("/")
    def dashboard() -> str:
        return render_template("index.html", settings=cfg)

    @app.get("/healthz")
    def healthz() -> Any:
        return jsonify({"ok": True, **service.health()})

    @app.get("/api/challenges")
    def list_challenges() -> Any:
        return jsonify({"items": service.list_challenges()})

    @app.get("/api/registry")
    def list_registry() -> Any:
        return jsonify({"items": service.list_challenges()})

    @app.post("/api/challenges")
    def register_challenge() -> Any:
        challenge = service.register_challenge(_read_json())
        _safe_record_log(
            service,
            message="container saved in registry",
            metadata={"challenge_id": challenge["challenge_id"], "image": challenge["image"]},
        )
        return jsonify(challenge), 201

    @app.post("/api/registry")
    def register_registry_item() -> Any:
        challenge = service.register_challenge(_read_json())
        _safe_record_log(
            service,
            message="container saved in registry",
            metadata={"challenge_id": challenge["challenge_id"], "image": challenge["image"]},
        )
        return jsonify(challenge), 201

    @app.get("/api/instances")
    def list_instances() -> Any:
        status = request.args.get("status")
        if status and status not in {"running", "stopped", "expired", "orphaned"}:
            raise ValidationError("status must be running|stopped|expired|orphaned")
        return jsonify({"items": service.list_instances(status=status)})

    @app.post("/api/instances/start")
    def start_instance() -> Any:
        payload = _read_json()
        result = service.start_instance(payload)
        _safe_record_log(
            service,
            message="instance start requested",
            metadata={
                "challenge_id": result.instance.get("challenge_id"),
                "user_id": result.instance.get("user_id"),
                "created": result.created,
                "instance_id": result.instance.get("instance_id"),
            },
        )
        code = 201 if result.created else 200
        return jsonify({"created": result.created, "instance": result.instance}), code

    @app.post("/api/registry/<registry_id>/start")
    def start_instance_from_registry(registry_id: str) -> Any:
        body = _read_json(optional=True) or {}
        payload = {
            "registry_id": registry_id,
            "user_id": body.get("user_id"),
        }
        result = service.start_instance(payload)
        _safe_record_log(
            service,
            message="instance start requested from registry endpoint",
            metadata={
                "registry_id": registry_id,
                "user_id": result.instance.get("user_id"),
                "created": result.created,
                "instance_id": result.instance.get("instance_id"),
            },
        )
        code = 201 if result.created else 200
        return jsonify({"created": result.created, "instance": result.instance}), code

    @app.post("/api/instances/<instance_id>/stop")
    def stop_instance(instance_id: str) -> Any:
        body = _read_json(optional=True)
        reason = "manual"
        if body:
            reason = str(body.get("reason", "manual"))
        stopped = service.stop_instance(instance_id, reason=reason)
        _safe_record_log(
            service,
            message="instance stop requested",
            metadata={
                "instance_id": instance_id,
                "reason": reason,
                "status": stopped.get("status"),
            },
        )
        return jsonify(stopped)

    @app.post("/api/instances/stop-all")
    def stop_all_instances() -> Any:
        body = _read_json(optional=True)
        reason = "bulk-stop"
        if body:
            reason = str(body.get("reason", "bulk-stop"))
        stopped = service.stop_all_running_instances(reason=reason)
        _safe_record_log(
            service,
            message="bulk stop requested",
            metadata={"reason": reason, "stopped": len(stopped)},
        )
        return jsonify({"stopped": len(stopped), "items": stopped})

    @app.post("/api/reaper/run")
    def run_reaper_now() -> Any:
        expired = service.reap_expired_instances()
        _safe_record_log(
            service,
            message="manual reaper run",
            metadata={"expired": len(expired)},
        )
        return jsonify({"expired": len(expired), "items": expired})

    @app.get("/api/logs")
    def list_logs() -> Any:
        raw_limit = request.args.get("limit", "200")
        try:
            limit = int(raw_limit)
        except ValueError as exc:
            raise ValidationError("limit must be an integer") from exc
        return jsonify({"items": service.list_logs(limit=limit)})

    @app.post("/api/logs")
    def create_log() -> Any:
        body = _read_json()
        created = service.record_log(
            message=body.get("message"),
            metadata=body.get("metadata"),
            level=str(body.get("level", "info")),
        )
        return jsonify(created), 201

    @app.delete("/api/logs")
    def clear_logs() -> Any:
        deleted = service.clear_logs()
        return jsonify({"deleted": deleted})

    @app.errorhandler(ValidationError)
    def handle_validation_error(exc: ValidationError) -> Any:
        _safe_record_log(
            service,
            message="validation error",
            metadata={"error": str(exc)},
            level="warn",
        )
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(NotFoundError)
    def handle_not_found_error(exc: NotFoundError) -> Any:
        _safe_record_log(
            service,
            message="resource not found",
            metadata={"error": str(exc)},
            level="warn",
        )
        return jsonify({"error": str(exc)}), 404

    @app.errorhandler(CapacityError)
    def handle_capacity_error(exc: CapacityError) -> Any:
        _safe_record_log(
            service,
            message="capacity error",
            metadata={"error": str(exc)},
            level="warn",
        )
        return jsonify({"error": str(exc)}), 409

    @app.errorhandler(BackendUnavailableError)
    def handle_backend_error(exc: BackendUnavailableError) -> Any:
        _safe_record_log(
            service,
            message="backend unavailable",
            metadata={"error": str(exc)},
            level="error",
        )
        return jsonify({"error": str(exc)}), 503

    return app


def _read_json(*, optional: bool = False) -> dict[str, Any] | None:
    payload = request.get_json(silent=True)
    if payload is None and optional:
        return None
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object")
    return payload


def main() -> None:
    settings = Settings.from_env()
    app = create_app(settings)
    app.run(host=settings.bind_host, port=settings.bind_port, debug=False)


if __name__ == "__main__":
    main()
