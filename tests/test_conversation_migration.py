from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import SecretStr
from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import Base, Database, migrate_sqlite_conversations
from ad_vista_agent.database.models import AnswerVersion, Feedback, Message


def test_migrate_legacy_sqlite_conversations_is_idempotent(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "conversations.sqlite3"
    with sqlite3.connect(sqlite_path) as source:
        source.executescript(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                source_path TEXT NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL,
                citations_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE feedback (
                feedback_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                message_id TEXT, decision TEXT NOT NULL, note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE answer_versions (
                version_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                message_id TEXT NOT NULL, answer_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO sessions VALUES
                ('session_1', 'run_1', 'video.mp4', '分析卖点', 'active', '2026-01-01', '2026-01-01');
            INSERT INTO messages VALUES
                ('msg_1', 'session_1', 'user', '分析卖点', '[]', '2026-01-01T00:00:01');
            INSERT INTO messages VALUES
                ('msg_2', 'session_1', 'assistant', '核心卖点是清洁', '["speech_0001"]', '2026-01-01T00:00:02');
            INSERT INTO feedback VALUES
                ('feedback_1', 'session_1', 'msg_2', 'approve', '正确', '2026-01-01T00:00:03');
            INSERT INTO answer_versions VALUES
                ('version_1', 'session_1', 'msg_2', '{"answer":"核心卖点是清洁"}', '2026-01-01T00:00:02');
            """
        )

    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'target.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        first = migrate_sqlite_conversations(sqlite_path, database)
        second = migrate_sqlite_conversations(sqlite_path, database)
        assert first["sessions"] == 1
        assert first["messages"] == 2
        assert first["feedback"] == 1
        assert first["answer_versions"] == 1
        assert second["messages"] == 0
        assert second["feedback"] == 0
        assert second["answer_versions"] == 0
        with database.session() as session:
            message = session.get(Message, "msg_2")
            feedback = session.get(Feedback, "feedback_1")
            version = session.get(AnswerVersion, "version_1")
            assert message is not None and message.content == "核心卖点是清洁"
            assert feedback is not None and feedback.decision == "approve"
            assert version is not None and version.answer_json["answer"] == "核心卖点是清洁"
    finally:
        database.close()
