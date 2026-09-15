from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from ad_vista_agent.config import DatabaseConfig


class Database:
    def __init__(self, config: DatabaseConfig) -> None:
        if config.url is None:
            raise ValueError("ADVISTA_DATABASE_URL is not configured")
        url = config.url.get_secret_value()
        options: dict[str, object] = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            # SQLite is used for local tests and compatibility only; its
            # SingletonThreadPool does not accept PostgreSQL pool options.
            if ":memory:" in url:
                options["poolclass"] = StaticPool
                options["connect_args"] = {"check_same_thread": False}
        else:
            options.update(
                pool_size=config.pool_size,
                max_overflow=config.max_overflow,
                pool_timeout=config.pool_timeout_seconds,
            )
        self.engine = create_engine(url, **options)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def check(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()
