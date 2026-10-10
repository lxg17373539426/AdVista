from __future__ import annotations

import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import event

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import (
    Base,
    Database,
    ExecutionRepository,
    RequestRepository,
    migrate_sqlite_conversations,
)
from ad_vista_agent.database.models import (
    AnswerVersion,
    Conversation,
    Feedback,
    Message,
    ToolCall,
)
from ad_vista_agent.memory import ConversationStore


@pytest.fixture
def database(tmp_path):
    value = Database(DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")))
    Base.metadata.create_all(value.engine)
    try:
        yield value
    finally:
        value.close()


def _request(database, suffix):
    requests = RequestRepository(database)
    requests.ensure_system_user()
    requests.ensure_conversation("conversation", user_id="system", run_id=None, conversation_type="general_chat")
    request, _ = requests.create_request(request_id=f"request_{suffix}", user_id="system", conversation_id="conversation", request_type="general_chat", input_json={}, idempotency_key=suffix)
    return request


def test_long_tool_call_primary_keys_fit_schema_and_sync_existing_records(database):
    request = _request(database, "long")
    executions = ExecutionRepository(database)
    execution_id = "exec_" + "e" * 32
    call_id = "call_" + "c" * 32
    executions.create(execution_id=execution_id, request_id=request.request_id, model_version=None, prompt_version=None)
    calls = [{"call_id": call_id, "tool": "test", "status": "completed", "arguments": {}}]
    executions.sync_tool_calls(execution_id, calls)
    executions.sync_tool_calls(execution_id, calls)
    records = executions.tool_calls(execution_id)
    assert len(records) == 1
    assert len(records[0].tool_call_id) <= ToolCall.__table__.c.tool_call_id.type.length
    with database.session() as session:
        session.add(ToolCall(tool_call_id="legacy_id", execution_id=execution_id, call_id="call_legacy", tool_name="test", status="running", attempt=1, input_summary_json={}))
    executions.sync_tool_calls(execution_id, [{"call_id": "call_legacy", "status": "completed"}])
    records = executions.tool_calls(execution_id)
    legacy = [record for record in records if record.call_id == "call_legacy"]
    assert len(legacy) == 1
    assert legacy[0].tool_call_id == "legacy_id"
    assert legacy[0].status == "completed"


def test_migration_preserves_historical_times_without_rewriting_existing_messages(database, tmp_path):
    source_path = tmp_path / "source.sqlite3"
    source = ConversationStore(source_path)
    source.create_session(session_id="historical", run_id="run_old", source_path=Path("video.mp4"), goal="old")
    message_id = source.add_message("historical", "assistant", "old", [])
    feedback_id = source.add_feedback("historical", "approve", "old", message_id)
    version_id = source.add_version("historical", message_id, {"answer": "old"})
    created = "2020-01-02T03:04:05+00:00"
    updated = "2020-01-02T03:05:06+00:00"
    with sqlite3.connect(source_path) as source_database:
        for table in ("sessions", "messages", "feedback", "answer_versions"):
            source_database.execute(f"UPDATE {table} SET created_at=?", (created,))
        source_database.execute("UPDATE sessions SET updated_at=?", (updated,))
    migrate_sqlite_conversations(source_path, database)
    expected_created = datetime.fromisoformat(created).replace(tzinfo=None)
    expected_updated = datetime.fromisoformat(updated).replace(tzinfo=None)
    with database.session() as session:
        for model, identifier in ((Message, message_id), (Feedback, feedback_id), (AnswerVersion, version_id), (Conversation, "historical")):
            assert session.get(model, identifier).created_at == expected_created
        assert session.get(Conversation, "historical").updated_at == expected_updated
        message = session.get(Message, message_id)
        session.get(Conversation, "historical").status = "archived"
        message.content = "subsequent edit"
        message.created_at = datetime(2021, 1, 1)  # noqa: DTZ001 — 测试 SQLite 历史无时区时间戳，必须保留该输入。
    migrate_sqlite_conversations(source_path, database)
    with database.session() as session:
        assert session.get(Conversation, "historical").status == "archived"
        assert session.get(Message, message_id).content == "subsequent edit"
        assert session.get(Message, message_id).created_at == datetime(2021, 1, 1)  # noqa: DTZ001 — 测试 SQLite 历史无时区时间戳，必须保留该输入。


def test_postgresql_concurrent_message_sequences():
    # Explicit opt-in: the isolation harness sets this to its own private socket.
    url = os.environ.get("ADVISTA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires the isolated PostgreSQL harness")
    database = Database(DatabaseConfig(url=SecretStr(url)))
    try:
        Base.metadata.create_all(database.engine)
        requests = RequestRepository(database)
        for index in range(8):
            _request(database, str(index))
        start = threading.Barrier(8)

        @event.listens_for(database.engine, "after_cursor_execute")
        def delay_sequence_allocation(_connection, _cursor, statement, _parameters, _context, _many):
            if "max(messages.sequence_no)" in statement:
                time.sleep(0.1)

        def add(index):
            start.wait(timeout=10)
            return requests.add_user_message(message_id=f"message_{index}", conversation_id="conversation", user_id="system", content=str(index), request_id=f"request_{index}").sequence_no

        with ThreadPoolExecutor(max_workers=8) as pool:
            sequences = list(pool.map(add, range(8)))
        assert sorted(sequences) == list(range(1, 9))
    finally:
        database.close()


def test_postgresql_long_tool_call_ids():
    url = os.environ.get("ADVISTA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires the isolated PostgreSQL harness")
    database = Database(DatabaseConfig(url=SecretStr(url)))
    try:
        Base.metadata.create_all(database.engine)
        test_long_tool_call_primary_keys_fit_schema_and_sync_existing_records(database)
    finally:
        database.close()
