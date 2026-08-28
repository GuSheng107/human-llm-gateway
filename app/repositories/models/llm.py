"""真实 LLM 配置：llm_configs。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db import Base
from ...domain.enums import LLMProtocol
from .base import TimestampMixin, sa_enum


class LlmConfig(TimestampMixin, Base):
    __tablename__ = "llm_configs"
    __table_args__ = (
        Index(
            "uq_llm_configs_owner_name",
            "owner_user_id",
            "name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_llm_configs_owner_enabled", "owner_user_id", "is_enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    protocol: Mapped[LLMProtocol] = mapped_column(sa_enum(LLMProtocol), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    real_model: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    headers_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    encryption_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
