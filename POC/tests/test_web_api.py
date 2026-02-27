from __future__ import annotations

from pathlib import Path

from poc_orchestrator.config import Settings
from poc_orchestrator.web import create_app


def test_api_happy_path(tmp_path: Path) -> None:
    settings = Settings(
        backend="mock",
        db_path=str(tmp_path / "api.db"),
        bind_host="127.0.0.1",
        bind_port=8000,
        reaper_interval_seconds=2,
        default_public_host="127.0.0.1",
    )
    app = create_app(settings)
    client = app.test_client()

    res = client.post(
        "/api/challenges",
        json={
            "challenge_id": "demo-http",
            "name": "Demo",
            "image": "poc-demo-http:latest",
            "container_port": 8080,
            "cpu_limit": 0.5,
            "memory_limit_mb": 256,
            "timeout_seconds": 120,
            "max_instances": 10,
        },
    )
    assert res.status_code == 201

    res = client.post(
        "/api/instances/start",
        json={"challenge_id": "demo-http", "user_id": "team-01"},
    )
    assert res.status_code == 201
    payload = res.get_json()
    assert payload["instance"]["status"] == "running"

    instance_id = payload["instance"]["instance_id"]
    res = client.post(f"/api/instances/{instance_id}/stop", json={"reason": "manual"})
    assert res.status_code == 200
    assert res.get_json()["status"] == "stopped"

    app.config["reaper"].shutdown()
    app.config["store"].close()


def test_stop_all_endpoint(tmp_path: Path) -> None:
    settings = Settings(
        backend="mock",
        db_path=str(tmp_path / "bulk-stop.db"),
        bind_host="127.0.0.1",
        bind_port=8001,
        reaper_interval_seconds=2,
        default_public_host="127.0.0.1",
    )
    app = create_app(settings)
    client = app.test_client()

    client.post(
        "/api/challenges",
        json={
            "challenge_id": "demo-http",
            "name": "Demo",
            "image": "poc-demo-http:latest",
            "container_port": 8080,
            "cpu_limit": 0.5,
            "memory_limit_mb": 256,
            "timeout_seconds": 120,
            "max_instances": 10,
        },
    )
    client.post(
        "/api/instances/start",
        json={"challenge_id": "demo-http", "user_id": "team-01"},
    )
    client.post(
        "/api/instances/start",
        json={"challenge_id": "demo-http", "user_id": "team-02"},
    )

    res = client.post("/api/instances/stop-all", json={"reason": "bulk-stop"})
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["stopped"] == 2

    running = client.get("/api/instances?status=running").get_json()
    assert running["items"] == []

    app.config["reaper"].shutdown()
    app.config["store"].close()


def test_registry_alias_endpoints(tmp_path: Path) -> None:
    settings = Settings(
        backend="mock",
        db_path=str(tmp_path / "registry.db"),
        bind_host="127.0.0.1",
        bind_port=8002,
        reaper_interval_seconds=2,
        default_public_host="127.0.0.1",
    )
    app = create_app(settings)
    client = app.test_client()

    res = client.post(
        "/api/registry",
        json={
            "challenge_id": "registry-demo",
            "name": "Registry Demo",
            "image": "poc-demo-http:latest",
            "container_port": 8080,
            "cpu_limit": 0.5,
            "memory_limit_mb": 256,
            "timeout_seconds": 120,
            "max_instances": 10,
        },
    )
    assert res.status_code == 201
    assert res.get_json()["challenge_id"] == "registry-demo"

    res = client.get("/api/registry")
    assert res.status_code == 200
    items = res.get_json()["items"]
    assert len(items) == 1
    assert items[0]["challenge_id"] == "registry-demo"

    res = client.post(
        "/api/registry/registry-demo/start",
        json={"user_id": "team-registry"},
    )
    assert res.status_code == 201
    payload = res.get_json()
    assert payload["instance"]["status"] == "running"
    assert payload["instance"]["challenge_id"] == "registry-demo"

    app.config["reaper"].shutdown()
    app.config["store"].close()


def test_log_endpoints(tmp_path: Path) -> None:
    settings = Settings(
        backend="mock",
        db_path=str(tmp_path / "logs.db"),
        bind_host="127.0.0.1",
        bind_port=8003,
        reaper_interval_seconds=2,
        default_public_host="127.0.0.1",
    )
    app = create_app(settings)
    client = app.test_client()

    res = client.post(
        "/api/logs",
        json={
            "message": "manual test log",
            "level": "info",
            "metadata": {"team": "team-01"},
        },
    )
    assert res.status_code == 201
    created = res.get_json()
    assert created["message"] == "manual test log"
    assert created["metadata"]["team"] == "team-01"

    res = client.get("/api/logs?limit=50")
    assert res.status_code == 200
    items = res.get_json()["items"]
    assert len(items) >= 1
    assert any(item["message"] == "manual test log" for item in items)

    res = client.delete("/api/logs")
    assert res.status_code == 200
    assert "deleted" in res.get_json()

    res = client.get("/api/logs")
    assert res.status_code == 200
    assert res.get_json()["items"] == []

    app.config["reaper"].shutdown()
    app.config["store"].close()
