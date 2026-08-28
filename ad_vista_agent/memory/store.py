from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    message_id TEXT,
                    decision TEXT NOT NULL CHECK(decision IN ('approve', 'reject', 'revise')),
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS answer_versions (
                    version_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    message_id TEXT NOT NULL REFERENCES messages(message_id),
                    answer_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1');
                """
            )

    def create_session(self, *, session_id: str, run_id: str, source_path: Path, goal: str) -> None:
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT run_id, source_path, goal FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            metadata = (run_id, str(source_path), goal)
            if existing is not None:
                stored = (existing["run_id"], existing["source_path"], existing["goal"])
                if stored != metadata:
                    raise ValueError(f"Conversation session metadata conflict: {session_id}")
                return
            connection.execute(
                """INSERT INTO sessions
                (session_id, run_id, source_path, goal, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)""",
                (session_id, run_id, str(source_path), goal, now, now),
            )

    def set_status(self, session_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                (status, _now(), session_id),
            )

    def add_message(self, session_id: str, role: str, content: str, citations: list[str]) -> str:
        message_id = f"msg_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO messages
                (message_id, session_id, role, content, citations_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (message_id, session_id, role, content, json.dumps(citations), _now()),
            )
            connection.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (_now(), session_id))
        return message_id

    def add_version(self, session_id: str, message_id: str, answer: dict[str, Any]) -> str:
        version_id = f"version_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO answer_versions
                (version_id, session_id, message_id, answer_json, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (version_id, session_id, message_id, json.dumps(answer, ensure_ascii=False), _now()),
            )
        return version_id

    def add_feedback(self, session_id: str, decision: str, note: str, message_id: str | None = None) -> str:
        if decision not in {"approve", "reject", "revise"}:
            raise ValueError(f"Unsupported feedback decision: {decision}")
        feedback_id = f"feedback_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO feedback
                (feedback_id, session_id, message_id, decision, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (feedback_id, session_id, message_id, decision, note, _now()),
            )
        return feedback_id

    def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT message_id, role, content, citations_json, created_at
                FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        return [
            {
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"],
                "citations": json.loads(row["citations_json"]),
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]

    def session(self, session_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"Unknown conversation session: {session_id}")
        return dict(row)
