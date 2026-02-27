"""Application configuration loaded from environment variables."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    backend: str
    db_path: str
    bind_host: str
    bind_port: int
    reaper_interval_seconds: int
    default_public_host: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            backend=os.getenv("POC_BACKEND", "docker").strip().lower(),
            db_path=os.getenv("POC_DB_PATH", "./poc.db").strip(),
            bind_host=os.getenv("POC_BIND_HOST", "127.0.0.1").strip(),
            bind_port=_as_int(os.getenv("POC_BIND_PORT", "8000"), 8000),
            reaper_interval_seconds=_as_int(
                os.getenv("POC_REAPER_INTERVAL_SECONDS", "10"),
                10,
            ),
            default_public_host=os.getenv("POC_PUBLIC_HOST", "127.0.0.1").strip(),
        )

    @property
    def is_mock_backend(self) -> bool:
        return self.backend == "mock"

    @property
    def reaper_interval(self) -> float:
        return max(_as_float(str(self.reaper_interval_seconds), 10.0), 1.0)
