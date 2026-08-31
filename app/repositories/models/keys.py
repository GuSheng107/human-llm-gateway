"""API Key 与模型集合：api_keys / api_key_fake_models。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db import Base
from ...domain.enums import DeliveryMode, ReplyStrategy
from .base import TimestampMixin, sa_enum, utc_now


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint(
            "(delivery_mode = 'im' AND im_connection_id IS NOT NULL) OR "
            "(delivery_mode = 'web' AND im_connection_id IS NULL)",
            name="ck_api_keys_delivery",
        ),
        CheckConstraint(
            "(reply_strategy IN ('llm', 'human_fallback_llm') AND llm_config_id IS NOT NULL) OR "
            "(reply_strategy = 'human' AND llm_config_id IS NULL)",
            name="ck_api_keys_strategy",
        ),
        CheckConstraint(
            "human_timeout_seconds >= 10 AND human_timeout_seconds <= 1800",
            name="ck_api_keys_timeout",
        ),
        Index(
            "uq_api_keys_owner_name",
            "owner_user_id",
            "name",
            unique=True,
        ),
        Index("ix_api_keys_owner", "owner_user_id"),
        Index("ix_api_keys_owner_enabled", "owner_user_id", "is_enabled"),
        Index("ix_api_keys_key_prefix", "key_prefix"),
        Index("ix_api_keys_im_connection", "im_connection_id"),
        Index("ix_api_keys_llm_config", "llm_config_id"),
        Index("ix_api_keys_model_group", "model_group_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    delivery_mode: Mapped[DeliveryMode] = mapped_column(sa_enum(DeliveryMode), nullable=False)
    im_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("im_connections.id"), nullable=True
    )
    reply_strategy: Mapped[ReplyStrategy] = mapped_column(sa_enum(ReplyStrategy), nullable=False)
    llm_config_id: Mapped[int | None] = mapped_column(ForeignKey("llm_configs.id"), nullable=True)
    human_timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    model_group_id: Mapped[int | None] = mapped_column(ForeignKey("model_groups.id"), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiKeyFakeModel(Base):
    __tablename__ = "api_key_fake_models"
    __table_args__ = (UniqueConstraint("api_key_id", "fake_model_id", name="uq_api_key_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False
    )
    fake_model_id: Mapped[int] = mapped_column(
        ForeignKey("fake_models.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
