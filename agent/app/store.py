"""Local SQLite persistence for runs, approved edits, and tool activity."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_database_path() -> Path:
    configured = os.getenv("DEVPILOT_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "DevPilot" / "devpilot.sqlite3"
    base = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "devpilot" / "devpilot.sqlite3"


class StateStore:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.path = Path(database_path) if database_path is not None else default_database_path()
        self.path = self.path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_runs (
                    id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('planning', 'pending_approval', 'approved', 'rejected', 'failed')),
                    plan TEXT,
                    model TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    rejected_at TEXT
                );
                CREATE INDEX IF NOT EXISTS task_runs_created_idx ON task_runs(created_at DESC);
                CREATE TABLE IF NOT EXISTS edit_previews (
                    id TEXT PRIMARY KEY,
                    task_id TEXT REFERENCES task_runs(id) ON DELETE SET NULL,
                    workspace_root TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    diff TEXT NOT NULL,
                    expected_sha256 TEXT,
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                );
                CREATE TABLE IF NOT EXISTS tool_events (
                    id TEXT PRIMARY KEY,
                    task_id TEXT REFERENCES task_runs(id) ON DELETE SET NULL,
                    tool_name TEXT NOT NULL,
                    succeeded INTEGER NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tool_events_task_idx ON tool_events(task_id, created_at);
                PRAGMA user_version = 1;
                """
            )

    @staticmethod
    def _id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_run(self, task: str, workspace_root: str) -> str:
        task_id = self._id()
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO task_runs(id,task,workspace_root,status,created_at,updated_at) VALUES(?,?,?,'planning',?,?)",
                (task_id, task, workspace_root, now, now),
            )
        return task_id

    def complete_run(self, task_id: str, plan: str, model: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE task_runs SET status='pending_approval',plan=?,model=?,updated_at=? WHERE id=? AND status='planning'",
                (plan, model, utc_now(), task_id),
            )
            if result.rowcount != 1:
                raise ValueError("task run could not be completed")

    def fail_run(self, task_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE task_runs SET status='failed',error=?,updated_at=? WHERE id=? AND status='planning'",
                (error[:2_000], utc_now(), task_id),
            )

    def get_run(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(connection.execute("SELECT * FROM task_runs WHERE id=?", (task_id,)).fetchone())

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,task,workspace_root,status,plan,model,error,created_at,updated_at,approved_at,rejected_at "
                "FROM task_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def approve_run(self, task_id: str) -> bool:
        now = utc_now()
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE task_runs SET status='approved',approved_at=?,updated_at=? WHERE id=? AND status='pending_approval'",
                (now, now, task_id),
            )
            return result.rowcount == 1

    def reject_run(self, task_id: str) -> bool:
        now = utc_now()
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE task_runs SET status='rejected',rejected_at=?,updated_at=? WHERE id=? AND status='pending_approval'",
                (now, now, task_id),
            )
            return result.rowcount == 1

    def create_edit_preview(
        self,
        workspace_root: str,
        path: str,
        content: str,
        diff: str,
        expected_sha256: str | None,
        task_id: str | None = None,
    ) -> str:
        preview_id = self._id()
        with self._connect() as connection:
            if task_id is not None:
                run = connection.execute("SELECT status FROM task_runs WHERE id=?", (task_id,)).fetchone()
                if run is None or run["status"] != "approved":
                    raise ValueError("the associated task plan must be approved before editing")
            connection.execute(
                "INSERT INTO edit_previews(id,task_id,workspace_root,path,content,diff,expected_sha256,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (preview_id, task_id, workspace_root, path, content, diff, expected_sha256, utc_now()),
            )
        return preview_id

    def get_edit_preview(self, preview_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._row(connection.execute("SELECT * FROM edit_previews WHERE id=?", (preview_id,)).fetchone())

    def mark_edit_applied(self, preview_id: str) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE edit_previews SET applied_at=? WHERE id=? AND applied_at IS NULL",
                (utc_now(), preview_id),
            )
            return result.rowcount == 1

    def record_tool_event(self, tool_name: str, succeeded: bool, duration_ms: int, task_id: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO tool_events(id,task_id,tool_name,succeeded,duration_ms,created_at) VALUES(?,?,?,?,?,?)",
                (self._id(), task_id, tool_name[:80], int(succeeded), max(0, duration_ms), utc_now()),
            )

    def list_tool_events(self, task_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,tool_name,succeeded,duration_ms,created_at FROM tool_events "
                "WHERE task_id=? ORDER BY created_at LIMIT ?",
                (task_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
