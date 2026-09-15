from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import inspect, text

from ad_vista_agent.config import DatabaseConfig, load_settings
from ad_vista_agent.database import Base, Database
from ad_vista_agent.database import models  # noqa: F401
from ad_vista_agent.database.models import User
from ad_vista_agent.web.service import WebService


def test_database_url_is_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("ADVISTA_DATABASE_URL", "sqlite+pysqlite:///:memory:")

    settings = load_settings()

    assert settings.database.configured
    assert settings.database.url is not None
    assert settings.database.url.get_secret_value() == "sqlite+pysqlite:///:memory:"
    assert "sqlite" not in str(settings.database.url)


def test_database_session_commits_and_rolls_back(tmp_path: Path) -> None:
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'service.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        with database.session() as session:
            session.add(
                User(
                    user_id="user_1",
                    username="first@example.test",
                    role="user",
                    status="active",
                    created_at=datetime.now(timezone.utc),
                )
            )
        with database.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1

        with pytest.raises(RuntimeError):
            with database.session() as session:
                session.add(
                    User(
                        user_id="user_2",
                        username="second@example.test",
                        role="user",
                        status="active",
                        created_at=datetime.now(timezone.utc),
                    )
                )
                raise RuntimeError("rollback")
        with database.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
    finally:
        database.close()


def test_initial_metadata_contains_service_tables() -> None:
    database = Database(DatabaseConfig(url=SecretStr("sqlite+pysqlite:///:memory:")))
    try:
        Base.metadata.create_all(database.engine)
        assert set(inspect(database.engine).get_table_names()) == {
            "answer_versions",
            "artifact_references",
            "context_versions",
            "conversations",
            "executions",
            "feedback",
            "messages",
            "requests",
            "tool_calls",
            "users",
        }
    finally:
        database.close()


def test_alembic_initial_migration_upgrades_and_downgrades(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "migration.db"
    monkeypatch.setenv(
        "ADVISTA_DATABASE_URL", f"sqlite+pysqlite:///{database_path}"
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(root / "alembic.ini")

    command.upgrade(config, "head")
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{database_path}"))
    )
    try:
        tables = set(inspect(database.engine).get_table_names())
        assert "users" in tables
        assert "context_versions" in tables
        assert "alembic_version" in tables
    finally:
        database.close()

    command.downgrade(config, "base")
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{database_path}"))
    )
    try:
        assert set(inspect(database.engine).get_table_names()) == {"alembic_version"}
    finally:
        database.close()


def test_web_service_keeps_database_optional(tmp_path: Path) -> None:
    base = load_settings()
    settings = base.model_copy(
        update={
            "paths": base.paths.model_copy(update={"output_root": tmp_path / "outputs"}),
            "database": DatabaseConfig(),
        }
    )
    service = WebService(settings)
    try:
        assert service.health() == {"status": "ok", "database": "not_configured"}
    finally:
        service.close()
