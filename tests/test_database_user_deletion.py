from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import Base, Database, ExecutionRepository, RequestRepository, UserRepository
from ad_vista_agent.database.models import User


def test_user_delete_removes_owned_records_but_protects_system(tmp_path: Path) -> None:
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'delete.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        users = UserRepository(database)
        user, _ = users.create_api_user("delete@example.test")
        requests = RequestRepository(database)
        requests.ensure_conversation(
            "conversation_delete",
            user_id=user.user_id,
            run_id="run_delete",
            conversation_type="video_analysis",
        )
        request, _ = requests.create_request(
            request_id="request_delete",
            user_id=user.user_id,
            conversation_id="conversation_delete",
            request_type="video_analysis",
            input_json={"goal": "delete"},
            idempotency_key="delete-idem",
        )
        ExecutionRepository(database).create(
            execution_id="exec_delete",
            request_id=request.request_id,
            model_version=None,
            prompt_version=None,
        )
        deleted = users.delete_user(user.user_id)
        assert deleted["conversation_ids"] == ["conversation_delete"]
        assert deleted["execution_ids"] == ["exec_delete"]
        with database.session() as session:
            assert session.get(User, user.user_id) is None
        with pytest.raises(ValueError, match="system user"):
            users.delete_user("system")
    finally:
        database.close()
