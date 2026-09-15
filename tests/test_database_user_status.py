from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import Base, Database, UserRepository


def test_revoked_api_user_cannot_authenticate(tmp_path: Path) -> None:
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'status.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        repository = UserRepository(database)
        user, api_key = repository.create_api_user("revoked@example.test")
        repository.set_status(user.user_id, "revoked")
        assert repository.authenticate(api_key) is None
        repository.set_status(user.user_id, "active")
        assert repository.authenticate(api_key) is not None
    finally:
        database.close()
