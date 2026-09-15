from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select

from .connection import Database
from .models import ContextVersion, Conversation, Execution


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ContextRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def publish(
        self,
        *,
        conversation_id: str,
        execution_id: str,
        schema_version: str,
        model_version: str | None,
        prompt_version: str | None,
        artifact_hashes: dict[str, str],
    ) -> ContextVersion:
        with self.database.session() as session:
            conversation = session.scalar(
                select(Conversation)
                .where(Conversation.conversation_id == conversation_id)
                .with_for_update()
            )
            if conversation is None:
                raise KeyError(conversation_id)
            if session.get(Execution, execution_id) is None:
                raise KeyError(execution_id)
            current = session.scalar(
                select(func.coalesce(func.max(ContextVersion.version), 0)).where(
                    ContextVersion.conversation_id == conversation_id
                )
            )
            version = int(current or 0) + 1
            value = ContextVersion(
                context_version_id=f"context_{secrets.token_hex(16)}",
                conversation_id=conversation_id,
                execution_id=execution_id,
                version=version,
                model_version=model_version,
                prompt_version=prompt_version,
                schema_version=schema_version,
                artifact_hashes_json=dict(artifact_hashes),
                created_at=_now(),
            )
            session.add(value)
            session.flush()
            session.expunge(value)
            return value

    def get(self, conversation_id: str, version: int) -> ContextVersion:
        with self.database.session() as session:
            value = session.scalar(
                select(ContextVersion).where(
                    ContextVersion.conversation_id == conversation_id,
                    ContextVersion.version == version,
                )
            )
            if value is None:
                raise KeyError(f"{conversation_id}:{version}")
            session.expunge(value)
            return value

    def latest(self, conversation_id: str) -> ContextVersion | None:
        with self.database.session() as session:
            value = session.scalar(
                select(ContextVersion)
                .where(ContextVersion.conversation_id == conversation_id)
                .order_by(ContextVersion.version.desc())
                .limit(1)
            )
            if value is None:
                return None
            session.expunge(value)
            return value
