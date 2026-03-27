#!/usr/bin/env python3
"""End-to-end smoke test for the CTFd container challenge plugin PoC."""

from __future__ import annotations

from dataclasses import dataclass
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


BASE_URL = (
    os.environ.get("BASE_URL")
    or os.environ.get("SMOKE_TEST_BASE_URL")
    or "http://127.0.0.1:8001"
).rstrip("/")
FLAG = "flag{container-poc}"
IMAGE_ARCHIVE_PATH = os.environ.get("IMAGE_ARCHIVE_PATH", "/tmp/poc-demo-http.tar")
EXPECTED_ACCESS_HOST = os.environ.get("SMOKE_TEST_EXPECT_ACCESS_HOST", "").strip() or None
EXPECTED_ACCESS_PORT_MIN = os.environ.get("SMOKE_TEST_EXPECT_ACCESS_PORT_MIN", "").strip() or None
EXPECTED_ACCESS_PORT_MAX = os.environ.get("SMOKE_TEST_EXPECT_ACCESS_PORT_MAX", "").strip() or None
SMOKE_TEST_DOCKER_HOST = os.environ.get("SMOKE_TEST_DOCKER_HOST", "").strip() or None
SMOKE_TEST_DOCKER_TLS_VERIFY = os.environ.get("SMOKE_TEST_DOCKER_TLS_VERIFY")
SMOKE_TEST_DOCKER_CERT_PATH = (
    os.environ.get("SMOKE_TEST_DOCKER_CERT_PATH", "").strip() or None
)


@dataclass
class Session:
    opener: Any

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str, dict[str, str]]:
        request = Request(
            BASE_URL + path,
            data=data,
            method=method,
            headers=headers or {},
        )
        try:
            response = self.opener.open(request, timeout=30)
            status = response.getcode()
            body = response.read().decode("utf-8", errors="replace")
            headers_out = dict(response.headers.items())
            return status, body, headers_out
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return exc.code, body, dict(exc.headers.items())


def new_session() -> Session:
    jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    return Session(opener=opener)


def extract_nonce(html: str) -> str:
    match = re.search(r'name="nonce"[^>]*value="([^"]+)"', html)
    if not match:
        raise RuntimeError("Unable to extract CSRF nonce")
    return match.group(1)


def extract_csrf_nonce(html: str) -> str:
    match = re.search(r"['\"]csrfNonce['\"]\s*:\s*['\"]([^'\"]+)['\"]", html)
    if not match:
        raise RuntimeError("Unable to extract JSON CSRF nonce")
    return match.group(1)


def env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return int(raw)


def wait_for_http_ready(timeout: float = 180.0) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            status, _, _ = new_session().request("/setup")
            if status in {200, 302}:
                return
        except (HTTPError, URLError, OSError) as exc:
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"CTFd never became ready: {last_error}")


def post_form(session: Session, path: str, form: dict[str, str]) -> tuple[int, str, dict[str, str]]:
    return session.request(
        path,
        method="POST",
        data=urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def post_multipart_file(
    session: Session,
    path: str,
    *,
    field_name: str,
    file_path: str,
    csrf_nonce: str,
) -> tuple[int, dict[str, Any]]:
    boundary = f"----ctfdcontainer{int(time.time() * 1000)}"
    nonce_part = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="nonce"\r\n\r\n'
        f"{csrf_nonce}\r\n"
    ).encode()
    with open(file_path, "rb") as handle:
        file_bytes = handle.read()

    filename = os.path.basename(file_path)
    file_part = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        "Content-Type: application/x-tar\r\n\r\n"
    ).encode() + file_bytes + b"\r\n"
    closing = f"--{boundary}--\r\n".encode()
    body = nonce_part + file_part + closing

    status, raw_body, _ = session.request(
        path,
        method="POST",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    try:
        parsed = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Expected JSON from multipart upload {path}, got: {raw_body}") from exc
    return status, parsed


def login(session: Session, *, name: str, password: str) -> None:
    status, html, _ = session.request("/login")
    if status != 200:
        raise RuntimeError(f"Unable to open login page: {status}")
    nonce = extract_nonce(html)
    status, body, _ = post_form(
        session,
        "/login",
        {"name": name, "password": password, "nonce": nonce},
    )
    if status not in {200, 302}:
        raise RuntimeError(f"Login failed with status {status}: {body}")


def register_user(session: Session, *, name: str, email: str, password: str) -> None:
    status, html, _ = session.request("/register")
    if status != 200:
        raise RuntimeError(f"Unable to open register page: {status}")
    nonce = extract_nonce(html)
    status, body, _ = post_form(
        session,
        "/register",
        {"name": name, "email": email, "password": password, "nonce": nonce},
    )
    if status not in {200, 302}:
        raise RuntimeError(f"Registration failed with status {status}: {body}")


def setup_ctfd(session: Session) -> None:
    status, html, _ = session.request("/setup")
    if status == 302:
        return
    if status != 200:
        raise RuntimeError(f"Unexpected setup page status {status}")
    if 'id="setup-form"' not in html:
        return
    nonce = extract_nonce(html)
    form = {
        "ctf_name": "Container Plugin PoC",
        "ctf_description": "Automated smoke test",
        "user_mode": "users",
        "challenge_visibility": "private",
        "account_visibility": "public",
        "score_visibility": "public",
        "registration_visibility": "public",
        "verify_emails": "false",
        "social_shares": "false",
        "team_size": "0",
        "ctf_theme": "core",
        "theme_color": "",
        "start": "",
        "end": "",
        "name": "admin",
        "email": "admin@example.com",
        "password": "adminpass",
        "nonce": nonce,
    }
    status, body, _ = post_form(session, "/setup", form)
    if status not in {200, 302}:
        raise RuntimeError(f"CTFd setup failed with status {status}: {body}")


def api_request(
    session: Session,
    *,
    method: str,
    path: str,
    csrf_nonce: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode()
    status, raw_body, _ = session.request(
        path,
        method=method,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "CSRF-Token": csrf_nonce,
        },
    )
    try:
        parsed = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Expected JSON from {path}, got: {raw_body}") from exc
    return status, parsed


def get_csrf_nonce(session: Session, path: str = "/") -> str:
    status, html, _ = session.request(path)
    if status != 200:
        raise RuntimeError(f"Unable to open {path}: {status}")
    return extract_csrf_nonce(html)


def wait_for_plugin_health(admin_session: Session, admin_csrf: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    last_payload = None
    while time.time() < deadline:
        status, payload = api_request(
            admin_session,
            method="GET",
            path="/plugins/ctfd_container_challenges/api/admin/instances",
            csrf_nonce=admin_csrf,
        )
        last_payload = payload
        if status == 200 and payload.get("success"):
            return
        time.sleep(2)
    raise RuntimeError(f"Plugin admin API never became ready: {last_payload}")


def wait_for_url(url: str, timeout: float = 60.0) -> str:
    deadline = time.time() + timeout
    opener = build_opener()
    last_error = ""
    while time.time() < deadline:
        try:
            response = opener.open(url, timeout=15)
            body = response.read().decode("utf-8", errors="replace")
            if response.getcode() == 200:
                return body
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"Runtime URL never became reachable: {url} ({last_error})")


def assert_access_url_properties(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError(f"Unexpected access URL scheme: {url}")
    if EXPECTED_ACCESS_HOST and parsed.hostname != EXPECTED_ACCESS_HOST:
        raise RuntimeError(
            f"Access URL host mismatch: expected {EXPECTED_ACCESS_HOST}, got {parsed.hostname}"
        )
    lower = env_int("SMOKE_TEST_EXPECT_ACCESS_PORT_MIN")
    upper = env_int("SMOKE_TEST_EXPECT_ACCESS_PORT_MAX")
    if lower is not None and (parsed.port is None or parsed.port < lower):
        raise RuntimeError(f"Access URL port {parsed.port} is below expected minimum {lower}")
    if upper is not None and (parsed.port is None or parsed.port > upper):
        raise RuntimeError(f"Access URL port {parsed.port} is above expected maximum {upper}")


def create_challenge(
    admin_session: Session,
    admin_csrf: str,
    *,
    name: str,
    image_tag: str,
    asset_token: str | None,
    timeout_seconds: int,
    max_instances: int,
) -> int:
    payload = {
        "name": name,
        "category": "Docker",
        "description": "Spawn a dedicated demo service and solve the flag.",
        "value": 100,
        "state": "visible",
        "type": "containerized",
        "connection_info": "Use the runtime panel to launch your personal instance.",
        "image": image_tag,
        "container_port": 8080,
        "cpu_limit": 0.5,
        "memory_limit_mb": 256,
        "timeout_seconds": timeout_seconds,
        "max_instances": max_instances,
    }
    if asset_token:
        payload["uploaded_image_token"] = asset_token
    status, created = api_request(
        admin_session,
        method="POST",
        path="/api/v1/challenges",
        csrf_nonce=admin_csrf,
        payload=payload,
    )
    if status not in {200, 201} or not created.get("success"):
        raise RuntimeError(f"Challenge creation failed: {status} {created}")
    return int(created["data"]["id"])


def create_flag(admin_session: Session, admin_csrf: str, challenge_id: int) -> None:
    status, flag_payload = api_request(
        admin_session,
        method="POST",
        path="/api/v1/flags",
        csrf_nonce=admin_csrf,
        payload={
            "challenge": challenge_id,
            "type": "static",
            "content": FLAG,
            "data": "",
        },
    )
    if status not in {200, 201} or not flag_payload.get("success"):
        raise RuntimeError(f"Flag creation failed: {status} {flag_payload}")


def start_runtime(
    player_session: Session,
    player_csrf: str,
    challenge_id: int,
    *,
    expected_status: int,
) -> dict[str, Any]:
    status, payload = api_request(
        player_session,
        method="POST",
        path=f"/plugins/ctfd_container_challenges/api/challenges/{challenge_id}/instance",
        csrf_nonce=player_csrf,
        payload={"challenge_id": challenge_id},
    )
    if status != expected_status or not payload.get("success"):
        raise RuntimeError(f"Runtime start failed: {status} {payload}")
    return payload


def stop_runtime(
    player_session: Session,
    player_csrf: str,
    challenge_id: int,
) -> dict[str, Any]:
    status, payload = api_request(
        player_session,
        method="DELETE",
        path=f"/plugins/ctfd_container_challenges/api/challenges/{challenge_id}/instance",
        csrf_nonce=player_csrf,
    )
    if status != 200 or not payload.get("success"):
        raise RuntimeError(f"Runtime stop failed: {status} {payload}")
    return payload


def get_active_instance(
    player_session: Session,
    player_csrf: str,
    challenge_id: int,
) -> dict[str, Any] | None:
    status, payload = api_request(
        player_session,
        method="GET",
        path=f"/plugins/ctfd_container_challenges/api/challenges/{challenge_id}/instance",
        csrf_nonce=player_csrf,
    )
    if status != 200 or not payload.get("success"):
        raise RuntimeError(f"Failed to fetch active instance: {status} {payload}")
    return payload.get("instance")


def wait_for_instance_absent(
    player_session: Session,
    player_csrf: str,
    challenge_id: int,
    *,
    timeout: float = 60.0,
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if get_active_instance(player_session, player_csrf, challenge_id) is None:
            return
        time.sleep(2)
    raise RuntimeError(f"Instance for challenge {challenge_id} did not disappear in time")


def wait_for_instance_record(
    admin_session: Session,
    admin_csrf: str,
    *,
    instance_id: str,
    expected_status: str,
    expected_reason: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_payload: dict[str, Any] | None = None
    while time.time() < deadline:
        status, payload = api_request(
            admin_session,
            method="GET",
            path="/plugins/ctfd_container_challenges/api/admin/instances",
            csrf_nonce=admin_csrf,
        )
        last_payload = payload
        if status == 200 and payload.get("success"):
            for item in payload.get("items", []):
                if item.get("instance_id") != instance_id:
                    continue
                if item.get("status") == expected_status and item.get("stop_reason") == expected_reason:
                    return item
        time.sleep(2)
    raise RuntimeError(
        f"Timed out waiting for instance {instance_id} to become "
        f"{expected_status}/{expected_reason}: {last_payload}"
    )


def wait_for_running_instances(
    admin_session: Session,
    admin_csrf: str,
    *,
    challenge_id: int,
    expected_count: int,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    deadline = time.time() + timeout
    last_payload: dict[str, Any] | None = None
    while time.time() < deadline:
        status, payload = api_request(
            admin_session,
            method="GET",
            path="/plugins/ctfd_container_challenges/api/admin/instances",
            csrf_nonce=admin_csrf,
        )
        last_payload = payload
        if status == 200 and payload.get("success"):
            matches = [
                item
                for item in payload.get("items", [])
                if int(item.get("challenge_id", 0)) == challenge_id and item.get("status") == "running"
            ]
            if len(matches) == expected_count:
                return matches
        time.sleep(2)
    raise RuntimeError(
        f"Timed out waiting for {expected_count} running instance(s) for challenge "
        f"{challenge_id}: {last_payload}"
    )


def _docker_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    if SMOKE_TEST_DOCKER_HOST:
        env["DOCKER_HOST"] = SMOKE_TEST_DOCKER_HOST
    if SMOKE_TEST_DOCKER_TLS_VERIFY is not None:
        env["DOCKER_TLS_VERIFY"] = SMOKE_TEST_DOCKER_TLS_VERIFY
    if SMOKE_TEST_DOCKER_CERT_PATH:
        env["DOCKER_CERT_PATH"] = SMOKE_TEST_DOCKER_CERT_PATH
    return env


def remove_uploaded_image(image_tag: str) -> None:
    try:
        import docker as docker_sdk
        from docker.errors import ImageNotFound
        from docker.tls import TLSConfig
    except ImportError:
        subprocess.run(
            ["docker", "rmi", "-f", image_tag],
            check=True,
            capture_output=True,
            env=_docker_subprocess_env(),
        )
        return

    client_kwargs: dict[str, Any] = {"timeout": 30}
    if SMOKE_TEST_DOCKER_HOST:
        client_kwargs["base_url"] = SMOKE_TEST_DOCKER_HOST
        tls_enabled = (
            str(SMOKE_TEST_DOCKER_TLS_VERIFY or "").strip().lower() in {"1", "true", "yes", "on"}
            or SMOKE_TEST_DOCKER_CERT_PATH is not None
        )
        if tls_enabled:
            if SMOKE_TEST_DOCKER_CERT_PATH:
                ca_cert = os.path.join(SMOKE_TEST_DOCKER_CERT_PATH, "ca.pem")
                cert_path = os.path.join(SMOKE_TEST_DOCKER_CERT_PATH, "cert.pem")
                key_path = os.path.join(SMOKE_TEST_DOCKER_CERT_PATH, "key.pem")
                tls_kwargs: dict[str, Any] = {
                    "verify": str(SMOKE_TEST_DOCKER_TLS_VERIFY or "").strip().lower()
                    in {"1", "true", "yes", "on"}
                }
                if os.path.exists(ca_cert):
                    tls_kwargs["ca_cert"] = ca_cert
                if os.path.exists(cert_path) and os.path.exists(key_path):
                    tls_kwargs["client_cert"] = (cert_path, key_path)
                client_kwargs["tls"] = TLSConfig(**tls_kwargs)
            else:
                client_kwargs["tls"] = True
        client = docker_sdk.DockerClient(**client_kwargs)
    else:
        client = docker_sdk.from_env(**client_kwargs)

    try:
        client.images.remove(image=image_tag, force=True)
    except ImageNotFound:
        return
    finally:
        client.close()


def build_player(name: str, index: int) -> tuple[Session, str]:
    session = new_session()
    register_user(
        session,
        name=name,
        email=f"{name}@example.com",
        password="playerpass",
    )
    player_csrf = get_csrf_nonce(session, "/challenges")
    return session, player_csrf


def main() -> int:
    wait_for_http_ready()

    setup_session = new_session()
    setup_ctfd(setup_session)

    admin_session = new_session()
    login(admin_session, name="admin", password="adminpass")
    admin_csrf = get_csrf_nonce(admin_session, "/admin/challenges/new")
    wait_for_plugin_health(admin_session, admin_csrf)
    if not os.path.exists(IMAGE_ARCHIVE_PATH):
        raise RuntimeError(f"Missing Docker archive for upload flow: {IMAGE_ARCHIVE_PATH}")

    status, upload_payload = post_multipart_file(
        admin_session,
        "/plugins/ctfd_container_challenges/api/admin/images/upload",
        field_name="image_archive",
        file_path=IMAGE_ARCHIVE_PATH,
        csrf_nonce=admin_csrf,
    )
    if status != 200 or not upload_payload.get("success"):
        raise RuntimeError(f"Archive upload failed: {status} {upload_payload}")
    asset = upload_payload["asset"]

    primary_challenge_id = create_challenge(
        admin_session,
        admin_csrf,
        name="Container Demo",
        image_tag=asset["image_tag"],
        asset_token=asset["asset_token"],
        timeout_seconds=120,
        max_instances=2,
    )
    create_flag(admin_session, admin_csrf, primary_challenge_id)

    remove_uploaded_image(asset["image_tag"])

    player1_session, player1_csrf = build_player("player1", 1)
    player2_session, player2_csrf = build_player("player2", 2)
    player3_session, player3_csrf = build_player("player3", 3)

    player1_start = start_runtime(
        player1_session,
        player1_csrf,
        primary_challenge_id,
        expected_status=201,
    )
    player1_instance = player1_start["instance"]
    assert_access_url_properties(player1_instance["access_url"])
    runtime_body = wait_for_url(player1_instance["access_url"])
    if "demo http challenge" not in runtime_body.lower():
        raise RuntimeError(f"Unexpected runtime response body: {runtime_body}")

    player1_second = start_runtime(
        player1_session,
        player1_csrf,
        primary_challenge_id,
        expected_status=200,
    )
    if player1_second.get("created") is not False:
        raise RuntimeError(f"Runtime idempotency check failed: {player1_second}")
    if player1_second["instance"]["instance_id"] != player1_instance["instance_id"]:
        raise RuntimeError("Second start did not reuse the active instance")

    player2_start = start_runtime(
        player2_session,
        player2_csrf,
        primary_challenge_id,
        expected_status=201,
    )
    player2_instance = player2_start["instance"]
    if player2_instance["instance_id"] == player1_instance["instance_id"]:
        raise RuntimeError("Different players unexpectedly received the same runtime instance")
    assert_access_url_properties(player2_instance["access_url"])
    wait_for_url(player2_instance["access_url"])

    wait_for_running_instances(
        admin_session,
        admin_csrf,
        challenge_id=primary_challenge_id,
        expected_count=2,
    )

    status, capacity_payload = api_request(
        player3_session,
        method="POST",
        path=f"/plugins/ctfd_container_challenges/api/challenges/{primary_challenge_id}/instance",
        csrf_nonce=player3_csrf,
        payload={"challenge_id": primary_challenge_id},
    )
    if status != 409 or capacity_payload.get("success") is not False:
        raise RuntimeError(f"Capacity guard failed: {status} {capacity_payload}")

    status, solve_payload = api_request(
        player1_session,
        method="POST",
        path="/api/v1/challenges/attempt",
        csrf_nonce=player1_csrf,
        payload={"challenge_id": primary_challenge_id, "submission": FLAG},
    )
    if status != 200 or solve_payload.get("data", {}).get("status") != "correct":
        raise RuntimeError(f"Challenge solve failed: {status} {solve_payload}")

    wait_for_instance_absent(player1_session, player1_csrf, primary_challenge_id)
    wait_for_instance_record(
        admin_session,
        admin_csrf,
        instance_id=player1_instance["instance_id"],
        expected_status="stopped",
        expected_reason="challenge-solved",
    )

    player3_start = start_runtime(
        player3_session,
        player3_csrf,
        primary_challenge_id,
        expected_status=201,
    )
    player3_instance = player3_start["instance"]
    if player3_instance["instance_id"] in {
        player1_instance["instance_id"],
        player2_instance["instance_id"],
    }:
        raise RuntimeError("Capacity recovery did not produce a new runtime instance")
    wait_for_url(player3_instance["access_url"])

    player2_stopped = stop_runtime(player2_session, player2_csrf, primary_challenge_id)
    if player2_stopped.get("instance", {}).get("instance_id") != player2_instance["instance_id"]:
        raise RuntimeError(f"Unexpected manual stop payload: {player2_stopped}")
    wait_for_instance_record(
        admin_session,
        admin_csrf,
        instance_id=player2_instance["instance_id"],
        expected_status="stopped",
        expected_reason="manual",
    )

    player3_stopped = stop_runtime(player3_session, player3_csrf, primary_challenge_id)
    if player3_stopped.get("instance", {}).get("instance_id") != player3_instance["instance_id"]:
        raise RuntimeError(f"Unexpected manual stop payload for player3: {player3_stopped}")
    wait_for_instance_record(
        admin_session,
        admin_csrf,
        instance_id=player3_instance["instance_id"],
        expected_status="stopped",
        expected_reason="manual",
    )

    timeout_challenge_id = create_challenge(
        admin_session,
        admin_csrf,
        name="Timeout Demo",
        image_tag=asset["image_tag"],
        asset_token=None,
        timeout_seconds=30,
        max_instances=1,
    )
    create_flag(admin_session, admin_csrf, timeout_challenge_id)

    timeout_start = start_runtime(
        player3_session,
        player3_csrf,
        timeout_challenge_id,
        expected_status=201,
    )
    timeout_instance = timeout_start["instance"]
    assert_access_url_properties(timeout_instance["access_url"])
    wait_for_url(timeout_instance["access_url"])
    wait_for_instance_absent(
        player3_session,
        player3_csrf,
        timeout_challenge_id,
        timeout=70,
    )
    wait_for_instance_record(
        admin_session,
        admin_csrf,
        instance_id=timeout_instance["instance_id"],
        expected_status="expired",
        expected_reason="timeout",
        timeout=70,
    )

    status, admin_logs = api_request(
        admin_session,
        method="GET",
        path="/plugins/ctfd_container_challenges/api/admin/logs",
        csrf_nonce=admin_csrf,
    )
    if status != 200 or not admin_logs.get("success") or not admin_logs.get("items"):
        raise RuntimeError(f"Admin logs endpoint did not return activity: {admin_logs}")

    print("Smoke test passed")
    print(
        json.dumps(
            {
                "primary_challenge_id": primary_challenge_id,
                "timeout_challenge_id": timeout_challenge_id,
                "instances": {
                    "player1": player1_instance["instance_id"],
                    "player2": player2_instance["instance_id"],
                    "player3": player3_instance["instance_id"],
                    "timeout": timeout_instance["instance_id"],
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        raise
