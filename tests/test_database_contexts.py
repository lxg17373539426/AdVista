from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import Base, ContextRepository, Database, ExecutionRepository, RequestRepository


def test_context_versions_are_monotonic_and_preserve_artifact_hashes(tmp_path: Path) -> None:
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'context.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        requests = RequestRepository(database)
        user = requests.ensure_system_user()
        requests.ensure_conversation(
            "conversation_context",
            user_id=user.user_id,
            run_id="run_context",
            conversation_type="video_analysis",
        )
        request, _ = requests.create_request(
            request_id="request_context",
            user_id=user.user_id,
            conversation_id="conversation_context",
            request_type="video_analysis",
            input_json={"goal": "test"},
            idempotency_key="context-1",
        )
        executions = ExecutionRepository(database)
        executions.create(
            execution_id="exec_context",
            request_id=request.request_id,
            model_version="model-v1",
            prompt_version="prompt-v1",
        )
        contexts = ContextRepository(database)
        first = contexts.publish(
            conversation_id="conversation_context",
            execution_id="exec_context",
            schema_version="0.1",
            model_version="model-v1",
            prompt_version="prompt-v1",
            artifact_hashes={"ledger": "a" * 64},
        )
        second = contexts.publish(
            conversation_id="conversation_context",
            execution_id="exec_context",
            schema_version="0.1",
            model_version="model-v2",
            prompt_version="prompt-v2",
            artifact_hashes={"ledger": "b" * 64},
        )
        assert first.version == 1
        assert second.version == 2
        assert contexts.get("conversation_context", 1).artifact_hashes_json == {"ledger": "a" * 64}
        latest = contexts.latest("conversation_context")
        assert latest is not None and latest.version == 2
    finally:
        database.close()
