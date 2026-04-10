#!/usr/bin/env python3
"""Generate an autonomous demo video for the CTFd container plugin PoC."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
ARTIFACTS_DIR = ROOT / "artifacts"
WORK_DIR = ARTIFACTS_DIR / "video-work"
CLIPS_DIR = WORK_DIR / "clips"
STATE_PATH = WORK_DIR / "state.json"
ARCHIVE_PATH = WORK_DIR / "poc-demo-http.tar"
OUTPUT_PATH = ARTIFACTS_DIR / "poc-ctfd-demo.mp4"
BASE_URL = "http://127.0.0.1:8001"
DEFAULT_PLAYBACK_STRETCH = 1.15
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "adminpass"
PLAYER_PASSWORD = "playerpass"
FLAG = "flag{container-poc}"
PRIMARY_CHALLENGE_NAME = "Container Demo"
TIMEOUT_CHALLENGE_NAME = "Timeout Demo"
COMPOSE_CMD = [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.local.yml",
]
CLIP_ORDER = [
    "intro",
    "hook",
    "admin_config_title",
    "admin_config",
    "reuse_title",
    "player_reuse",
    "player2_start",
    "player3_capacity",
    "cleanup_title",
    "player1_solve",
    "admin_force_stop",
    "timeout_start",
    "timeout_result",
    "admin_overview",
    "split_host",
    "outro",
]


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


def log(message: str) -> None:
    print(f"[video] {message}", flush=True)


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    log(f"run: {' '.join(command)}")
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture_output,
    )


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
    if match:
        return match.group(1)
    raise RuntimeError("Unable to extract JSON CSRF nonce")


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
    file_path: Path,
    csrf_nonce: str,
) -> tuple[int, dict[str, Any]]:
    boundary = f"----ctfdcontainer{int(time.time() * 1000)}"
    nonce_part = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="nonce"\r\n\r\n'
        f"{csrf_nonce}\r\n"
    ).encode()
    file_bytes = file_path.read_bytes()
    filename = file_path.name
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
    return status, json.loads(raw_body) if raw_body else {}


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
    return status, json.loads(raw_body) if raw_body else {}


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
        "ctf_description": "Automated video generation",
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
        "name": ADMIN_USERNAME,
        "email": "admin@example.com",
        "password": ADMIN_PASSWORD,
        "nonce": nonce,
    }
    status, body, _ = post_form(session, "/setup", form)
    if status not in {200, 302}:
        raise RuntimeError(f"CTFd setup failed with status {status}: {body}")


def get_csrf_nonce(session: Session, path: str) -> str:
    status, html, _ = session.request(path)
    if status != 200:
        raise RuntimeError(f"Unable to open {path}: {status}")
    return extract_csrf_nonce(html)


def wait_for_plugin_health(admin_session: Session, admin_csrf: str, timeout: float = 120.0) -> None:
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
            return
        time.sleep(2)
    raise RuntimeError(f"Plugin admin API never became ready: {last_payload}")


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
    payload: dict[str, Any] = {
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


def remove_uploaded_image(image_tag: str) -> None:
    subprocess.run(
        ["docker", "rmi", "-f", image_tag],
        check=True,
        text=True,
        capture_output=True,
    )


def docker_cleanup() -> None:
    subprocess.run(
        COMPOSE_CMD + ["down", "-v"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    containers = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=ctfd.plugin=ctfd_container_challenges"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip().splitlines()
    if containers:
        subprocess.run(["docker", "rm", "-f", *containers], check=False, capture_output=True, text=True)

    networks = subprocess.run(
        ["docker", "network", "ls", "-q", "--filter", "label=ctfd.plugin=ctfd_container_challenges"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip().splitlines()
    if networks:
        subprocess.run(["docker", "network", "rm", *networks], check=False, capture_output=True, text=True)


def ensure_tools() -> None:
    if shutil.which("ffmpeg") is None:
        run(["brew", "install", "ffmpeg"], cwd=REPO_ROOT)
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe is required after ffmpeg installation")
    if shutil.which("node") is None or shutil.which("npm") is None:
        raise RuntimeError("Node.js and npm are required")
    if not (ROOT / "node_modules" / "playwright").exists():
        run(["npm", "install"], cwd=ROOT)
    run(["npx", "playwright", "install", "chromium"], cwd=ROOT)


def build_demo_archive() -> Path:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    run(["docker", "build", "-t", "poc-demo-http:latest", str(REPO_ROOT / "POC" / "challenges" / "demo-http")])
    run(["docker", "save", "-o", str(ARCHIVE_PATH), "poc-demo-http:latest"])
    return ARCHIVE_PATH


def seed_state() -> dict[str, Any]:
    setup_session = new_session()
    setup_ctfd(setup_session)

    admin_session = new_session()
    login(admin_session, name=ADMIN_USERNAME, password=ADMIN_PASSWORD)
    admin_csrf = get_csrf_nonce(admin_session, "/admin/challenges/new")
    wait_for_plugin_health(admin_session, admin_csrf)

    status, upload_payload = post_multipart_file(
        admin_session,
        "/plugins/ctfd_container_challenges/api/admin/images/upload",
        field_name="image_archive",
        file_path=ARCHIVE_PATH,
        csrf_nonce=admin_csrf,
    )
    if status != 200 or not upload_payload.get("success"):
        raise RuntimeError(f"Archive upload failed: {status} {upload_payload}")
    asset = upload_payload["asset"]

    primary_challenge_id = create_challenge(
        admin_session,
        admin_csrf,
        name=PRIMARY_CHALLENGE_NAME,
        image_tag=asset["image_tag"],
        asset_token=asset["asset_token"],
        timeout_seconds=120,
        max_instances=2,
    )
    create_flag(admin_session, admin_csrf, primary_challenge_id)

    timeout_challenge_id = create_challenge(
        admin_session,
        admin_csrf,
        name=TIMEOUT_CHALLENGE_NAME,
        image_tag="poc-demo-http:latest",
        asset_token=None,
        timeout_seconds=30,
        max_instances=1,
    )
    create_flag(admin_session, admin_csrf, timeout_challenge_id)

    remove_uploaded_image(asset["image_tag"])

    users = {
        "player1": {"name": "player1", "email": "player1@example.com", "password": PLAYER_PASSWORD},
        "player2": {"name": "player2", "email": "player2@example.com", "password": PLAYER_PASSWORD},
        "player3": {"name": "player3", "email": "player3@example.com", "password": PLAYER_PASSWORD},
        "timeoutplayer": {
            "name": "timeoutplayer",
            "email": "timeoutplayer@example.com",
            "password": PLAYER_PASSWORD,
        },
    }

    user_sessions: dict[str, Session] = {}
    user_csrfs: dict[str, str] = {}
    for key, user in users.items():
        session = new_session()
        register_user(session, name=user["name"], email=user["email"], password=user["password"])
        user_sessions[key] = session
        user_csrfs[key] = get_csrf_nonce(session, "/challenges")

    state = {
        "base_url": BASE_URL,
        "archive_path": str(ARCHIVE_PATH),
        "output_path": str(OUTPUT_PATH),
        "clips_dir": str(CLIPS_DIR),
        "primary_challenge": {
            "id": primary_challenge_id,
            "name": PRIMARY_CHALLENGE_NAME,
            "flag": FLAG,
        },
        "timeout_challenge": {
            "id": timeout_challenge_id,
            "name": TIMEOUT_CHALLENGE_NAME,
        },
        "admin": {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        "users": users,
    }

    STATE_PATH.write_text(json.dumps(state, indent=2))

    return {
        "state": state,
        "admin_session": admin_session,
        "admin_csrf": admin_csrf,
        "user_sessions": user_sessions,
        "user_csrfs": user_csrfs,
    }


def record_clip(name: str) -> None:
    run(
        [
            "node",
            "./scripts/video_capture.cjs",
            "--config",
            str(STATE_PATH),
            "--clip",
            name,
        ],
        cwd=ROOT,
    )


def assemble_video(playback_stretch: float) -> None:
    concat_path = WORK_DIR / "clips.txt"
    lines = []
    for clip_name in CLIP_ORDER:
        clip_path = CLIPS_DIR / f"{clip_name}.webm"
        if not clip_path.exists():
            raise RuntimeError(f"Missing clip output: {clip_path}")
        lines.append(f"file '{clip_path.as_posix()}'")
    concat_path.write_text("\n".join(lines) + "\n")
    video_filter = f"setpts={playback_stretch:.4f}*PTS,fps=30,format=yuv420p"

    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-an",
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-movflags",
            "+faststart",
            str(OUTPUT_PATH),
        ],
        cwd=ROOT,
    )


def validate_video() -> None:
    if not OUTPUT_PATH.exists():
        raise RuntimeError(f"Expected video output at {OUTPUT_PATH}")
    if OUTPUT_PATH.stat().st_size == 0:
        raise RuntimeError("Generated video is empty")
    probe = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(OUTPUT_PATH),
        ],
        cwd=ROOT,
        capture_output=True,
    )
    duration = float(probe.stdout.strip() or "0")
    if duration < 60:
        raise RuntimeError(f"Generated video is unexpectedly short: {duration:.2f}s")
    log(f"video duration: {duration:.2f}s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-stack",
        action="store_true",
        help="Leave the CTFd stack running after generation.",
    )
    parser.add_argument(
        "--assemble-only",
        action="store_true",
        help="Reuse existing recorded clips and rebuild only the final MP4.",
    )
    parser.add_argument(
        "--playback-stretch",
        type=float,
        default=DEFAULT_PLAYBACK_STRETCH,
        help="Multiply clip timing during final export. Values above 1.0 make the video slower.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.playback_stretch < 1.0:
        raise RuntimeError("--playback-stretch must be at least 1.0")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    if OUTPUT_PATH.exists():
        OUTPUT_PATH.unlink()

    if args.assemble_only:
        assemble_video(args.playback_stretch)
        validate_video()
        log(f"generated video: {OUTPUT_PATH}")
        return 0

    for existing in CLIPS_DIR.iterdir():
        if existing.is_file():
            existing.unlink()

    ensure_tools()
    docker_cleanup()
    build_demo_archive()
    run(COMPOSE_CMD + ["up", "-d", "--build"], cwd=ROOT)
    wait_for_http_ready()
    seeded = seed_state()

    try:
        record_clip("intro")
        record_clip("hook")
        player1_instance = get_active_instance(
            seeded["user_sessions"]["player1"],
            seeded["user_csrfs"]["player1"],
            seeded["state"]["primary_challenge"]["id"],
        )
        if not player1_instance:
            raise RuntimeError("player1 instance was not created during hook clip")

        record_clip("admin_config_title")
        record_clip("admin_config")
        record_clip("reuse_title")
        record_clip("player_reuse")
        record_clip("player2_start")

        player2_instance = get_active_instance(
            seeded["user_sessions"]["player2"],
            seeded["user_csrfs"]["player2"],
            seeded["state"]["primary_challenge"]["id"],
        )
        if not player2_instance:
            raise RuntimeError("player2 instance was not created during capacity clip")

        record_clip("player3_capacity")
        record_clip("cleanup_title")
        record_clip("player1_solve")

        wait_for_instance_absent(
            seeded["user_sessions"]["player1"],
            seeded["user_csrfs"]["player1"],
            seeded["state"]["primary_challenge"]["id"],
        )
        wait_for_instance_record(
            seeded["admin_session"],
            seeded["admin_csrf"],
            instance_id=player1_instance["instance_id"],
            expected_status="stopped",
            expected_reason="challenge-solved",
        )

        record_clip("admin_force_stop")
        wait_for_instance_record(
            seeded["admin_session"],
            seeded["admin_csrf"],
            instance_id=player2_instance["instance_id"],
            expected_status="stopped",
            expected_reason="admin-force-stop",
        )

        record_clip("timeout_start")
        timeout_instance = get_active_instance(
            seeded["user_sessions"]["timeoutplayer"],
            seeded["user_csrfs"]["timeoutplayer"],
            seeded["state"]["timeout_challenge"]["id"],
        )
        if not timeout_instance:
            raise RuntimeError("timeout challenge instance was not created")

        wait_for_instance_record(
            seeded["admin_session"],
            seeded["admin_csrf"],
            instance_id=timeout_instance["instance_id"],
            expected_status="expired",
            expected_reason="timeout",
            timeout=70,
        )
        record_clip("timeout_result")
        record_clip("admin_overview")
        record_clip("split_host")
        record_clip("outro")
        assemble_video(args.playback_stretch)
        validate_video()
        log(f"generated video: {OUTPUT_PATH}")
    finally:
        if not args.keep_stack:
            docker_cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
