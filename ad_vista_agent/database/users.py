from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import delete, select

from .connection import Database
from .models import AnswerVersion, ContextVersion, Conversation, Execution, Feedback, Message, Request, ToolCall, User


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


class UserRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_api_user(self, username: str, *, role: str = "user") -> tuple[User, str]:
        normalized = username.strip()
        if not normalized:
            raise ValueError("Username must not be empty")
        api_key = f"avk_{secrets.token_urlsafe(32)}"
        user = User(
            user_id=f"user_{secrets.token_hex(12)}",
            username=normalized,
            credential_hash=hash_api_key(api_key),
            role=role,
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        with self.database.session() as session:
            session.add(user)
            session.flush()
        return user, api_key

    def authenticate(self, api_key: str) -> User | None:
        if not api_key:
            return None
        with self.database.session() as session:
            user = session.scalar(
                select(User).where(
                    User.credential_hash == hash_api_key(api_key),
                    User.status == "active",
                )
            )
            if user is None:
                return None
            user.last_login_at = datetime.now(timezone.utc)
            session.flush()
            session.expunge(user)
            return user

    def delete_user(self, user_id: str) -> dict[str, list[str]]:
        if user_id == "system":
            raise ValueError("The system user cannot be deleted")
        with self.database.session() as session:
            user = session.get(User, user_id)
            if user is None:
                raise KeyError(user_id)
            conversations = list(
                session.scalars(
                    select(Conversation.conversation_id).where(Conversation.user_id == user_id)
                )
            )
            conversation_ids = [str(item) for item in conversations]
            requests = list(
                session.scalars(select(Request).where(Request.user_id == user_id))
            )
            request_ids = [item.request_id for item in requests]
            executions = list(
                session.scalars(
                    select(Execution).where(Execution.request_id.in_(request_ids))
                )
            ) if request_ids else []
            execution_ids = [item.execution_id for item in executions]
            if execution_ids:
                session.execute(delete(ToolCall).where(ToolCall.execution_id.in_(execution_ids)))
            if conversation_ids:
                session.execute(delete(ContextVersion).where(ContextVersion.conversation_id.in_(conversation_ids)))
                session.execute(delete(AnswerVersion).where(AnswerVersion.conversation_id.in_(conversation_ids)))
                session.execute(delete(Feedback).where(Feedback.conversation_id.in_(conversation_ids)))
                session.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
            if execution_ids:
                session.execute(delete(Execution).where(Execution.execution_id.in_(execution_ids)))
            if request_ids:
                session.execute(delete(Request).where(Request.request_id.in_(request_ids)))
            if conversation_ids:
                session.execute(delete(Conversation).where(Conversation.conversation_id.in_(conversation_ids)))
            session.delete(user)
            return {"conversation_ids": conversation_ids, "execution_ids": execution_ids}

    def set_status(self, user_id: str, status: str) -> None:
        if status not in {"active", "revoked", "disabled"}:
            raise ValueError(f"Unsupported user status: {status}")
        if user_id == "system" and status != "active":
            raise ValueError("The system user must remain active")
        with self.database.session() as session:
            user = session.get(User, user_id)
            if user is None:
                raise KeyError(user_id)
            user.status = status
