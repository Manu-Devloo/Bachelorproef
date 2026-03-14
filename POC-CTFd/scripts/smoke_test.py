#!/usr/bin/env python3
"""End-to-end smoke test for the CTFd container challenge plugin PoC."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
import http.cookiejar


BASE_URL = "http://127.0.0.1:8001"
FLAG = "flag{container-poc}"
IMAGE_ARCHIVE_PATH = os.environ.get("IMAGE_ARCHIVE_PATH", "/tmp/poc-demo-http.tar")


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
    with open(file_path, "rb") as handle:
        file_bytes = handle.read()

    filename = os.path.basename(file_path)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        "Content-Type: application/x-tar\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    status, raw_body, _ = session.request(
        path,
        method="POST",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "CSRF-Token": csrf_nonce,
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

    status, created = api_request(
        admin_session,
        method="POST",
        path="/api/v1/challenges",
        csrf_nonce=admin_csrf,
        payload={
            "name": "Container Demo",
            "category": "Docker",
            "description": "Spawn a dedicated demo service and solve the flag.",
            "value": 100,
            "state": "visible",
            "type": "containerized",
            "connection_info": "Use the runtime panel to launch your personal instance.",
            "image": asset["image_tag"],
            "uploaded_image_token": asset["asset_token"],
            "container_port": 8080,
            "cpu_limit": 0.5,
            "memory_limit_mb": 256,
            "timeout_seconds": 120,
            "max_instances": 5,
        },
    )
    if status not in {200, 201} or not created.get("success"):
        raise RuntimeError(f"Challenge creation failed: {status} {created}")
    challenge_id = created["data"]["id"]

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

    subprocess.run(["docker", "rmi", "-f", asset["image_tag"]], check=True, capture_output=True)

    player_session = new_session()
    register_user(
        player_session,
        name="player1",
        email="player1@example.com",
        password="playerpass",
    )
    player_csrf = get_csrf_nonce(player_session, "/challenges")

    status, start_payload = api_request(
        player_session,
        method="POST",
        path=f"/plugins/ctfd_container_challenges/api/challenges/{challenge_id}/instance",
        csrf_nonce=player_csrf,
        payload={"challenge_id": challenge_id},
    )
    if status != 201 or not start_payload.get("success"):
        raise RuntimeError(f"Runtime start failed: {status} {start_payload}")

    instance = start_payload["instance"]
    runtime_body = wait_for_url(instance["access_url"])
    if "demo http challenge" not in runtime_body.lower():
        raise RuntimeError(f"Unexpected runtime response body: {runtime_body}")

    status, second_start = api_request(
        player_session,
        method="POST",
        path=f"/plugins/ctfd_container_challenges/api/challenges/{challenge_id}/instance",
        csrf_nonce=player_csrf,
        payload={"challenge_id": challenge_id},
    )
    if status != 200 or second_start.get("created") is not False:
        raise RuntimeError(f"Runtime idempotency check failed: {status} {second_start}")
    if second_start["instance"]["instance_id"] != instance["instance_id"]:
        raise RuntimeError("Second start did not reuse the active instance")

    status, solve_payload = api_request(
        player_session,
        method="POST",
        path="/api/v1/challenges/attempt",
        csrf_nonce=player_csrf,
        payload={"challenge_id": challenge_id, "submission": FLAG},
    )
    if status != 200 or solve_payload.get("data", {}).get("status") != "correct":
        raise RuntimeError(f"Challenge solve failed: {status} {solve_payload}")

    status, active_payload = api_request(
        player_session,
        method="GET",
        path=f"/plugins/ctfd_container_challenges/api/challenges/{challenge_id}/instance",
        csrf_nonce=player_csrf,
    )
    if status != 200 or active_payload.get("instance") is not None:
        raise RuntimeError(f"Active instance was not cleaned up after solve: {active_payload}")

    status, admin_instances = api_request(
        admin_session,
        method="GET",
        path="/plugins/ctfd_container_challenges/api/admin/instances",
        csrf_nonce=admin_csrf,
    )
    if status != 200:
        raise RuntimeError(f"Unable to list admin instances: {admin_instances}")

    matched = [item for item in admin_instances["items"] if item["instance_id"] == instance["instance_id"]]
    if not matched:
        raise RuntimeError("Solved instance was not recorded in admin runtime history")
    latest = matched[0]
    if latest["status"] != "stopped" or latest["stop_reason"] != "challenge-solved":
        raise RuntimeError(f"Unexpected final instance state: {latest}")

    print("Smoke test passed")
    print(json.dumps({"challenge_id": challenge_id, "instance_id": instance["instance_id"]}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        raise
