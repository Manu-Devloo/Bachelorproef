from __future__ import annotations

from pathlib import Path
from threading import Event, Lock, Thread
import time

from poc_orchestrator.backend import InMemoryBackend
from poc_orchestrator.service import CapacityError, OrchestratorService
from poc_orchestrator.storage import SQLiteStore


def build_service(tmp_path: Path, backend: InMemoryBackend | None = None) -> OrchestratorService:
    db_path = tmp_path / "test.db"
    store = SQLiteStore(str(db_path))
    service_backend = backend or InMemoryBackend()
    return OrchestratorService(store=store, backend=service_backend, public_host="127.0.0.1")


class SlowCountingBackend(InMemoryBackend):
    def __init__(self, *, delay_seconds: float = 0.1) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds
        self.start_calls = 0
        self._start_lock = Lock()

    def start(self, **kwargs):  # type: ignore[override]
        with self._start_lock:
            self.start_calls += 1
        time.sleep(self.delay_seconds)
        return super().start(**kwargs)


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

    logs = service.list_logs(limit=10)
    assert any(log["message"] == "instance expired by timeout" for log in logs)


def test_concurrent_start_is_idempotent_and_single_backend_launch(tmp_path: Path) -> None:
    backend = SlowCountingBackend()
    service = build_service(tmp_path, backend=backend)
    service.register_challenge(
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

    start_gate = Event()
    results: list[tuple[bool, str]] = []
    results_lock = Lock()

    def worker() -> None:
        start_gate.wait()
        result = service.start_instance({"challenge_id": "demo-http", "user_id": "team-01"})
        with results_lock:
            results.append((result.created, result.instance["instance_id"]))

    threads = [Thread(target=worker), Thread(target=worker)]
    for thread in threads:
        thread.start()
    start_gate.set()
    for thread in threads:
        thread.join()

    assert backend.start_calls == 1
    assert len(results) == 2
    assert sorted(created for created, _ in results) == [False, True]
    assert len({instance_id for _, instance_id in results}) == 1
