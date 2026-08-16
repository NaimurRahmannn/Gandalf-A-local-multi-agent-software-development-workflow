"""SQLite persistence for projects, dashboard phases, events, and approvals."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from orchestrator.memory import utc_now
from orchestrator.models import ApprovalStatus, DashboardStatus


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    root_path TEXT NOT NULL UNIQUE,
    memory_path TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phases (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    orchestrator_phase_id TEXT UNIQUE,
    phase_dir TEXT,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    current_agent TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_phases_project ON phases(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_phases_status ON phases(status);
CREATE TABLE IF NOT EXISTS phase_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_id TEXT NOT NULL REFERENCES phases(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    current_agent TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_phase ON phase_events(phase_id, id);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    phase_id TEXT NOT NULL REFERENCES phases(id) ON DELETE CASCADE,
    gate TEXT NOT NULL,
    status TEXT NOT NULL,
    feedback TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(phase_id, gate)
);
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    phase_id TEXT REFERENCES phases(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


class DashboardDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_project(self, name: str, slug: str, root: Path, memory: Path) -> dict[str, Any]:
        project_id = uuid4().hex
        created = utc_now()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO projects(id,name,slug,root_path,memory_path,created_at) VALUES(?,?,?,?,?,?)",
                (project_id, name, slug, str(root), str(memory), created),
            )
        return self.get_project(project_id) or {}

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM phases ph WHERE ph.project_id=p.id) phase_count "
                "FROM projects p ORDER BY p.created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return self._dict(row)

    def create_phase(self, project_id: str, prompt: str) -> dict[str, Any]:
        phase_id = uuid4().hex
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO phases(id,project_id,prompt,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (phase_id, project_id, prompt, DashboardStatus.CREATED, now, now),
            )
            connection.execute(
                "INSERT INTO phase_events(phase_id,status,message,created_at) VALUES(?,?,?,?)",
                (phase_id, DashboardStatus.CREATED, "Phase created", now),
            )
        return self.get_phase(phase_id) or {}

    def list_phases(self, project_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM phases"
        parameters: tuple[Any, ...] = ()
        if project_id:
            query += " WHERE project_id=?"
            parameters = (project_id,)
        query += " ORDER BY created_at DESC"
        with self.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def get_phase(self, phase_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM phases WHERE id=?", (phase_id,)).fetchone()
        return self._dict(row)

    def bind_orchestrator_phase(self, phase_id: str, orchestrator_id: str, phase_dir: Path) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE phases SET orchestrator_phase_id=?,phase_dir=?,updated_at=? WHERE id=?",
                (orchestrator_id, str(phase_dir), utc_now(), phase_id),
            )

    def transition(
        self,
        phase_id: str,
        status: DashboardStatus,
        current_agent: str | None,
        message: str,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        completed = now if status in {DashboardStatus.COMPLETED, DashboardStatus.FAILED} else None
        with self.connection() as connection:
            connection.execute(
                "UPDATE phases SET status=?,current_agent=?,error=?,updated_at=?,completed_at=COALESCE(?,completed_at) WHERE id=?",
                (status, current_agent, error, now, completed, phase_id),
            )
            connection.execute(
                "INSERT INTO phase_events(phase_id,status,current_agent,message,created_at) VALUES(?,?,?,?,?)",
                (phase_id, status, current_agent, message, now),
            )

    def reopen_failed_phase(self, phase_id: str) -> bool:
        """Atomically reopen a failed phase while retaining its event history."""

        now = utc_now()
        message = "Resume requested; continuing from the persisted workflow action"
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE phases SET status=?,current_agent=NULL,error=NULL,updated_at=?,"
                "completed_at=NULL WHERE id=? AND status=?",
                (
                    DashboardStatus.CREATED,
                    now,
                    phase_id,
                    DashboardStatus.FAILED,
                ),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                "INSERT INTO phase_events(phase_id,status,current_agent,message,created_at) "
                "VALUES(?,?,?,?,?)",
                (phase_id, DashboardStatus.CREATED, None, message, now),
            )
        return True

    def list_events(self, phase_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM phase_events WHERE phase_id=? AND id>? ORDER BY id",
                (phase_id, after_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def request_approval(self, phase_id: str, gate: str) -> dict[str, Any]:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO approvals(id,phase_id,gate,status,created_at) VALUES(?,?,?,?,?)",
                (uuid4().hex, phase_id, gate, ApprovalStatus.PENDING, utc_now()),
            )
        return self.get_approval(phase_id, gate) or {}

    def get_approval(self, phase_id: str, gate: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE phase_id=? AND gate=?", (phase_id, gate)
            ).fetchone()
        return self._dict(row)

    def list_approvals(self, phase_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE phase_id=? ORDER BY created_at", (phase_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def resolve_approval(
        self, phase_id: str, gate: str, status: ApprovalStatus, feedback: str
    ) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE approvals SET status=?,feedback=?,resolved_at=? "
                "WHERE phase_id=? AND gate=? AND status=?",
                (status, feedback, utc_now(), phase_id, gate, ApprovalStatus.PENDING),
            )
        return cursor.rowcount == 1

    def add_notification(
        self, project_id: str, phase_id: str | None, level: str, message: str
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO notifications(id,project_id,phase_id,level,message,created_at) VALUES(?,?,?,?,?,?)",
                (uuid4().hex, project_id, phase_id, level, message, utc_now()),
            )

    def list_notifications(self, unread_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM notifications"
        if unread_only:
            query += " WHERE is_read=0"
        query += " ORDER BY created_at DESC LIMIT 200"
        with self.connection() as connection:
            rows = connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def mark_notification_read(self, notification_id: str) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,)
            )
        return cursor.rowcount == 1
