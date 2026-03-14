"""SQLite persistence for challenges and running instances."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import sqlite3
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class StoreStats:
    challenge_count: int
    instance_count: int


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._init_schema()

    @contextmanager
    def locked(self) -> Any:
        with self._lock:
            yield

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    image TEXT NOT NULL,
                    container_port INTEGER NOT NULL,
                    cpu_limit REAL NOT NULL,
                    memory_limit_mb INTEGER NOT NULL,
                    timeout_seconds INTEGER NOT NULL,
                    max_instances INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS instances (
                    instance_id TEXT PRIMARY KEY,
                    challenge_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    container_id TEXT NOT NULL,
                    network_name TEXT NOT NULL,
                    host_port INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    stopped_at TEXT,
                    stop_reason TEXT,
                    FOREIGN KEY(challenge_id) REFERENCES challenges(challenge_id)
                );

                CREATE TABLE IF NOT EXISTS event_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_instances_status
                    ON instances(status);
                CREATE INDEX IF NOT EXISTS idx_instances_challenge
                    ON instances(challenge_id);
                CREATE INDEX IF NOT EXISTS idx_instances_user
                    ON instances(user_id);
                CREATE INDEX IF NOT EXISTS idx_event_logs_created_at
                    ON event_logs(created_at DESC);
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert_challenge(self, challenge: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO challenges (
                    challenge_id, name, image, container_port,
                    cpu_limit, memory_limit_mb, timeout_seconds,
                    max_instances, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(challenge_id) DO UPDATE SET
                    name = excluded.name,
                    image = excluded.image,
                    container_port = excluded.container_port,
                    cpu_limit = excluded.cpu_limit,
                    memory_limit_mb = excluded.memory_limit_mb,
                    timeout_seconds = excluded.timeout_seconds,
                    max_instances = excluded.max_instances,
                    updated_at = excluded.updated_at
                """,
                (
                    challenge["challenge_id"],
                    challenge["name"],
                    challenge["image"],
                    challenge["container_port"],
                    challenge["cpu_limit"],
                    challenge["memory_limit_mb"],
                    challenge["timeout_seconds"],
                    challenge["max_instances"],
                    challenge["created_at"],
                    challenge["updated_at"],
                ),
            )
            self._conn.commit()
        return self.get_challenge(challenge["challenge_id"])

    def list_challenges(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM challenges ORDER BY challenge_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_challenge(self, challenge_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM challenges WHERE challenge_id = ?",
                (challenge_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO instances (
                    instance_id, challenge_id, user_id, container_id,
                    network_name, host_port, status, started_at,
                    expires_at, stopped_at, stop_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance["instance_id"],
                    instance["challenge_id"],
                    instance["user_id"],
                    instance["container_id"],
                    instance["network_name"],
                    instance["host_port"],
                    instance["status"],
                    instance["started_at"],
                    instance["expires_at"],
                    instance.get("stopped_at"),
                    instance.get("stop_reason"),
                ),
            )
            self._conn.commit()
        return self.get_instance(instance["instance_id"])

    def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM instances WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_instances(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM instances WHERE status = ? ORDER BY started_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM instances ORDER BY started_at DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def get_running_instance(
        self,
        *,
        challenge_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM instances
                WHERE challenge_id = ? AND user_id = ? AND status = 'running'
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (challenge_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def count_running_instances_for_challenge(self, challenge_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM instances
                WHERE challenge_id = ? AND status = 'running'
                """,
                (challenge_id,),
            ).fetchone()
        return int(row["count"]) if row else 0

    def list_expired_running_instances(self, now_iso: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM instances
                WHERE status = 'running' AND expires_at <= ?
                ORDER BY expires_at ASC
                """,
                (now_iso,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_instance_stopped(
        self,
        *,
        instance_id: str,
        stop_reason: str,
        stopped_at: str,
        final_status: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE instances
                SET status = ?, stop_reason = ?, stopped_at = ?
                WHERE instance_id = ?
                """,
                (final_status, stop_reason, stopped_at, instance_id),
            )
            self._conn.commit()
        return self.get_instance(instance_id)

    def add_log(
        self,
        *,
        level: str,
        message: str,
        metadata_json: str | None,
        created_at: str,
    ) -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO event_logs (level, message, metadata_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (level, message, metadata_json, created_at),
            )
            self._conn.commit()
            log_id = int(cursor.lastrowid)
            row = self._conn.execute(
                "SELECT * FROM event_logs WHERE log_id = ?",
                (log_id,),
            ).fetchone()
        return dict(row) if row else {}

    def list_logs(self, *, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM event_logs
                ORDER BY log_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_logs(self) -> int:
        with self._lock:
            before = self._conn.total_changes
            self._conn.execute("DELETE FROM event_logs")
            self._conn.commit()
            deleted = self._conn.total_changes - before
        return int(deleted)

    def stats(self) -> StoreStats:
        with self._lock:
            challenge_row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM challenges"
            ).fetchone()
            instance_row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM instances"
            ).fetchone()
        return StoreStats(
            challenge_count=int(challenge_row["count"]),
            instance_count=int(instance_row["count"]),
        )
