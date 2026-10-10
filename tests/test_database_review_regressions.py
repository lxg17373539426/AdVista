from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import event

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import (
    Base,
    Database,
    ExecutionRepository,
    RequestRepository,
    UserRepository,
)
from ad_vista_agent.database.models import ArtifactReference, Execution, User


def test_user_deletion_removes_execution_artifacts_and_keeps_other_users(tmp_path):
    database = Database(DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'delete.db'}")))

    @event.listens_for(database.engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        Base.metadata.create_all(database.engine)
        users = UserRepository(database)
        owner, _ = users.create_api_user("owner")
        other, _ = users.create_api_user("other")
        requests = RequestRepository(database)
        executions = ExecutionRepository(database)
        for user in (owner, other):
            request_id = f"request_{user.username}"
            requests.create_request(
                request_id=request_id, user_id=user.user_id, conversation_id=None,
                request_type="video_analysis", input_json={}, idempotency_key="unique",
            )
            executions.create(
                execution_id=f"exec_{user.username}", request_id=request_id,
                model_version=None, prompt_version=None,
            )
            with database.session() as session:
                session.add(ArtifactReference(
                    artifact_id=f"artifact_{user.username}", execution_id=f"exec_{user.username}",
                    artifact_type="report", uri=f"{user.username}.html", sha256="a" * 64,
                    created_at=datetime.now(UTC),
                ))
        users.delete_user(owner.user_id)
        with database.session() as session:
            assert session.get(ArtifactReference, "artifact_owner") is None
            assert session.get(Execution, "exec_owner") is None
            assert session.get(User, owner.user_id) is None
            assert session.get(ArtifactReference, "artifact_other") is not None
            assert session.get(Execution, "exec_other") is not None
            assert session.get(User, other.user_id) is not None
    finally:
        database.close()


@pytest.mark.parametrize("operation", ["feedback", "context", "diagnostic_feedback"])
def test_conversation_references_reject_other_users(tmp_path, operation):
    from ad_vista_agent.database import ContextRepository
    from ad_vista_agent.database.conversations import PostgresConversationStore

    database = Database(DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'ownership.db'}")))
    try:
        Base.metadata.create_all(database.engine)
        users = UserRepository(database)
        owner, _ = users.create_api_user("owner")
        other, _ = users.create_api_user("other")
        requests = RequestRepository(database)
        for user in (owner, other):
            store = PostgresConversationStore(database, user_id=user.user_id)
            store.create_session(session_id=f"session_{user.username}", run_id=f"run_{user.username}", source_path=tmp_path, goal="test")
            store.add_message(f"session_{user.username}", "assistant", "test", [], message_id=f"message_{user.username}")
            requests.create_request(
                request_id=f"request_{user.username}", user_id=user.user_id,
                conversation_id=f"session_{user.username}", request_type="video_analysis",
                input_json={}, idempotency_key="unique",
            )
            ExecutionRepository(database).create(
                execution_id=f"exec_{user.username}", request_id=f"request_{user.username}",
                model_version=None, prompt_version=None,
            )
        if operation == "feedback":
            store = PostgresConversationStore(database, user_id=owner.user_id)
            with pytest.raises(KeyError):
                store.add_feedback("session_owner", "approve", "test", message_id="message_other")
            store.add_feedback("session_owner", "approve", "test", message_id="message_owner")
        elif operation == "diagnostic_feedback":
            from ad_vista_agent.database import DiagnosticRepository
            diagnostics = DiagnosticRepository(database)
            arguments = {"feedback_id": "feedback_test", "user_id": owner.user_id, "conversation_id": "session_owner", "execution_id": "exec_owner", "message_id": "message_other", "decision": "approve", "category": None, "note": "test"}
            with pytest.raises(KeyError):
                diagnostics.add_feedback(**arguments)
            diagnostics.add_feedback(**(arguments | {"message_id": "message_owner"}))
        else:
            contexts = ContextRepository(database)
            with pytest.raises(PermissionError):
                contexts.publish(conversation_id="session_owner", execution_id="exec_other", schema_version="1", model_version=None, prompt_version=None, artifact_hashes={})
            contexts.publish(conversation_id="session_owner", execution_id="exec_owner", schema_version="1", model_version=None, prompt_version=None, artifact_hashes={})
    finally:
        database.close()
