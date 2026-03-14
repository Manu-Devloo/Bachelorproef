"""SQLite persistence for active plugin-managed instances and logs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import sqlite3
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class StoreStats:
    instance_count: int
    running_count: int
    log_count: int


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
                CREATE TABLE IF NOT EXISTS instances (
                    instance_id TEXT PRIMARY KEY,
                    challenge_id INTEGER NOT NULL,
                    account_id TEXT NOT NULL,
                    account_type TEXT NOT NULL,
                    user_id INTEGER,
                    team_id INTEGER,
                    container_id TEXT NOT NULL,
                    network_name TEXT NOT NULL,
                    host_port INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    stopped_at TEXT,
                    stop_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS event_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS image_assets (
                    asset_token TEXT PRIMARY KEY,
                    challenge_id INTEGER,
                    original_filename TEXT NOT NULL,
                    archive_path TEXT NOT NULL,
                    image_tag TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_instances_status
                    ON instances(status);
                CREATE INDEX IF NOT EXISTS idx_instances_challenge
                    ON instances(challenge_id);
                CREATE INDEX IF NOT EXISTS idx_instances_account
                    ON instances(account_id);
                CREATE INDEX IF NOT EXISTS idx_logs_created_at
                    ON event_logs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_image_assets_challenge
                    ON image_assets(challenge_id);
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO instances (
                    instance_id, challenge_id, account_id, account_type,
                    user_id, team_id, container_id, network_name, host_port,
                    status, started_at, expires_at, stopped_at, stop_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance["instance_id"],
                    instance["challenge_id"],
                    instance["account_id"],
                    instance["account_type"],
                    instance.get("user_id"),
                    instance.get("team_id"),
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

    def get_running_instance(
        self,
        *,
        challenge_id: int,
        account_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM instances
                WHERE challenge_id = ? AND account_id = ? AND status = 'running'
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (challenge_id, account_id),
            ).fetchone()
        return dict(row) if row else None

    def count_running_instances_for_challenge(self, challenge_id: int) -> int:
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

    def list_instances(
        self,
        *,
        status: str | None = None,
        challenge_id: int | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM instances"
        params: list[Any] = []
        clauses: list[str] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if challenge_id is not None:
            clauses.append("challenge_id = ?")
            params.append(challenge_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY started_at DESC"
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

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

    def upsert_image_asset(
        self,
        *,
        asset_token: str,
        original_filename: str,
        archive_path: str,
        image_tag: str,
        created_at: str,
        updated_at: str,
        challenge_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO image_assets (
                    asset_token, challenge_id, original_filename, archive_path,
                    image_tag, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_token) DO UPDATE SET
                    challenge_id = excluded.challenge_id,
                    original_filename = excluded.original_filename,
                    archive_path = excluded.archive_path,
                    image_tag = excluded.image_tag,
                    updated_at = excluded.updated_at
                """,
                (
                    asset_token,
                    challenge_id,
                    original_filename,
                    archive_path,
                    image_tag,
                    created_at,
                    updated_at,
                ),
            )
            self._conn.commit()
        return self.get_image_asset(asset_token)

    def get_image_asset(self, asset_token: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM image_assets WHERE asset_token = ?",
                (asset_token,),
            ).fetchone()
        return dict(row) if row else None

    def get_image_asset_for_challenge(self, challenge_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM image_assets
                WHERE challenge_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (challenge_id,),
            ).fetchone()
        return dict(row) if row else None

    def bind_image_asset(self, *, asset_token: str, challenge_id: int, updated_at: str) -> dict[str, Any] | None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE image_assets
                SET challenge_id = ?, updated_at = ?
                WHERE asset_token = ?
                """,
                (challenge_id, updated_at, asset_token),
            )
            self._conn.commit()
        return self.get_image_asset(asset_token)

    def delete_image_asset(self, asset_token: str) -> dict[str, Any] | None:
        existing = self.get_image_asset(asset_token)
        if not existing:
            return None
        with self._lock:
            self._conn.execute(
                "DELETE FROM image_assets WHERE asset_token = ?",
                (asset_token,),
            )
            self._conn.commit()
        return existing

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

    def stats(self) -> StoreStats:
        with self._lock:
            instance_row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM instances"
            ).fetchone()
            running_row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM instances WHERE status = 'running'"
            ).fetchone()
            log_row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM event_logs"
            ).fetchone()
        return StoreStats(
            instance_count=int(instance_row["count"]),
            running_count=int(running_row["count"]),
            log_count=int(log_row["count"]),
        )
