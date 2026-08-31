"""Fake Model 目录与分组：fake_models / model_groups / model_group_items。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db import Base
from ...domain.enums import BillingTier, FakeModelScope, ModelEndpointType
from .base import TimestampMixin, sa_enum, utc_now


class FakeModel(TimestampMixin, Base):
    __tablename__ = "fake_models"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'system' AND owner_user_id IS NULL) OR "
            "(scope = 'private' AND owner_user_id IS NOT NULL)",
            name="ck_fake_models_scope_owner",
        ),
        Index(
            "uq_fake_models_system_model",
            "model_id",
            unique=True,
            sqlite_where=text("scope = 'system' AND deleted_at IS NULL"),
        ),
        Index(
            "uq_fake_models_private_model",
            "owner_user_id",
            "model_id",
            unique=True,
            sqlite_where=text("scope = 'private' AND deleted_at IS NULL"),
        ),
        Index("ix_fake_models_scope_enabled_sort", "scope", "is_enabled", "sort_order"),
        Index("ix_fake_models_owner_enabled_sort", "owner_user_id", "is_enabled", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[FakeModelScope] = mapped_column(sa_enum(FakeModelScope), nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owned_by: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # ---- 模型广场展示字段（仅管理台展示用，不进入 /v1/models 协议）----
    input_price_per_million: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    output_price_per_million: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    cached_input_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    cached_write_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    billing_tier: Mapped[BillingTier] = mapped_column(
        sa_enum(BillingTier), default=BillingTier.PAY_AS_YOU_GO, nullable=False
    )
    endpoint_type: Mapped[ModelEndpointType] = mapped_column(
        sa_enum(ModelEndpointType), default=ModelEndpointType.OPENAI_CHAT, nullable=False
    )
    logo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelGroup(TimestampMixin, Base):
    __tablename__ = "model_groups"
    __table_args__ = (
        Index(
            "uq_model_groups_owner_name",
            "owner_user_id",
            "name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 公开分组对所有用户可见可用（只读）；私有分组仅 owner 可见。
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelGroupItem(Base):
    __tablename__ = "model_group_items"
    __table_args__ = (UniqueConstraint("model_group_id", "fake_model_id", name="uq_group_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_group_id: Mapped[int] = mapped_column(
        ForeignKey("model_groups.id", ondelete="CASCADE"), nullable=False
    )
    fake_model_id: Mapped[int] = mapped_column(
        ForeignKey("fake_models.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
