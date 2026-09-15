from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import Base, Database, UserRepository, hash_api_key


def test_api_key_is_hashed_and_authentication_updates_last_login(tmp_path: Path) -> None:
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'users.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        user, api_key = UserRepository(database).create_api_user("person@example.test")

        assert user.user_id.startswith("user_")
        assert user.credential_hash is not None
        assert user.credential_hash == hash_api_key(api_key)
        assert api_key not in user.credential_hash
        authenticated = UserRepository(database).authenticate(api_key)
        assert authenticated is not None
        assert authenticated.username == "person@example.test"
        assert UserRepository(database).authenticate("avk_invalid") is None
    finally:
        database.close()
