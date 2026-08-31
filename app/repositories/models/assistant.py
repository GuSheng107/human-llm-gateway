"""Web 小助手：assistant_sessions / assistant_messages。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db import Base
from ...domain.enums import AssistantRole
from .base import TimestampMixin, sa_enum, utc_now


class AssistantSession(TimestampMixin, Base):
    __tablename__ = "assistant_sessions"
    __table_args__ = (Index("ix_assistant_sessions_owner", "owner_user_id", "last_message_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    llm_config_id: Mapped[int | None] = mapped_column(ForeignKey("llm_configs.id"), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        CheckConstraint(
            "(page_context_json IS NOT NULL AND page_feature IS NOT NULL "
            "AND context_version IS NOT NULL) OR "
            "(page_context_json IS NULL AND page_feature IS NULL AND context_version IS NULL)",
            name="ck_assistant_messages_page_context",
        ),
        Index("ix_assistant_messages_session", "session_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("assistant_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[AssistantRole] = mapped_column(sa_enum(AssistantRole), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    page_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_route: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_feature: Mapped[str | None] = mapped_column(String(100), nullable=True)
    context_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upstream_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
