"""Simple challenge service used for orchestrator smoke tests."""

from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os

FLAG = os.getenv("FLAG", "flag{poc-demo-not-real-flag}")
MESSAGE = os.getenv(
    "CHALLENGE_MESSAGE",
    "Exploit path omitted in PoC. This service exists to validate orchestration.",
)
PORT = int(os.getenv("PORT", "8080"))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, "text/plain; charset=utf-8", "ok")
            return

        body = f"""
        <html>
          <head><title>PoC Challenge</title></head>
          <body style=\"font-family: sans-serif; background: #111; color: #f8f8f2; padding: 2rem;\">
            <h1>Demo HTTP Challenge</h1>
            <p>{escape(MESSAGE)}</p>
            <p>Flag placeholder: <code>{escape(FLAG)}</code></p>
          </body>
        </html>
        """.strip()
        self._send(200, "text/html; charset=utf-8", body)

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send(self, status: int, content_type: str, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
