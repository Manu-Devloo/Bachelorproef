from __future__ import annotations

from pathlib import Path

from poc_orchestrator.backend import InMemoryBackend
from poc_orchestrator.service import CapacityError, OrchestratorService
from poc_orchestrator.storage import SQLiteStore


def build_service(tmp_path: Path) -> OrchestratorService:
    db_path = tmp_path / "test.db"
    store = SQLiteStore(str(db_path))
    backend = InMemoryBackend()
    return OrchestratorService(store=store, backend=backend, public_host="127.0.0.1")


def test_register_and_start_instance_is_idempotent(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    challenge = service.register_challenge(
        {
            "challenge_id": "demo-http",
            "name": "Demo HTTP",
            "image": "poc-demo-http:latest",
            "container_port": 8080,
            "cpu_limit": 0.5,
            "memory_limit_mb": 256,
            "timeout_seconds": 60,
            "max_instances": 3,
        }
    )
    assert challenge["challenge_id"] == "demo-http"

    first = service.start_instance({"challenge_id": "demo-http", "user_id": "team-01"})
    second = service.start_instance({"challenge_id": "demo-http", "user_id": "team-01"})

    assert first.created is True
    assert second.created is False
    assert first.instance["instance_id"] == second.instance["instance_id"]


def test_respects_max_instances_limit(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    service.register_challenge(
        {
            "challenge_id": "tiny-limit",
            "name": "Tiny Limit",
            "image": "poc-demo-http:latest",
            "container_port": 8080,
            "cpu_limit": 0.5,
            "memory_limit_mb": 256,
            "timeout_seconds": 60,
            "max_instances": 1,
        }
    )

    service.start_instance({"challenge_id": "tiny-limit", "user_id": "team-01"})

    try:
        service.start_instance({"challenge_id": "tiny-limit", "user_id": "team-02"})
        raised = False
    except CapacityError:
        raised = True

    assert raised is True


def test_reaper_expires_instance(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    service.register_challenge(
        {
            "challenge_id": "expire-me",
            "name": "Expire Me",
            "image": "poc-demo-http:latest",
            "container_port": 8080,
            "cpu_limit": 0.5,
            "memory_limit_mb": 256,
            "timeout_seconds": 60,
            "max_instances": 2,
        }
    )

    started = service.start_instance({"challenge_id": "expire-me", "user_id": "team-01"})
    instance_id = started.instance["instance_id"]

    # Force expiry to simulate timeout without sleeping.
    service.store._conn.execute(
        "UPDATE instances SET expires_at = '2000-01-01T00:00:00Z' WHERE instance_id = ?",
        (instance_id,),
    )
    service.store._conn.commit()

    expired = service.reap_expired_instances()
    assert len(expired) == 1
    assert expired[0]["status"] == "expired"
