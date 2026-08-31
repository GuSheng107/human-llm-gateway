"""真实 LLM 配置：llm_configs。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db import Base
from ...domain.enums import LLMProtocol, ThinkingLevel, ThinkingMode
from .base import TimestampMixin, sa_enum


class LlmConfig(TimestampMixin, Base):
    __tablename__ = "llm_configs"
    __table_args__ = (
        Index(
            "uq_llm_configs_owner_name",
            "owner_user_id",
            "name",
            unique=True,
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
    # ---- 采样与思考配置（留空 = 跟随请求/上游默认）----
    default_temperature: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    default_top_p: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    default_top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ---- 上下文窗口（仅展示与 admission 参考用）----
    context_window_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_window_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ---- 工具调用与输入能力 ----
    max_tool_call_rounds: Mapped[int] = mapped_column(Integer, default=16, nullable=False)
    supports_image_input: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # ---- 思考模式：thinking_level 仅 OpenAI Responses 有效 ----
    thinking_mode: Mapped[ThinkingMode] = mapped_column(
        sa_enum(ThinkingMode), default=ThinkingMode.MODEL_DEFAULT, nullable=False
    )
    thinking_level: Mapped[ThinkingLevel | None] = mapped_column(
        sa_enum(ThinkingLevel), nullable=True
    )
    # ---- 透传给上游的厂商私有参数 ----
    extra_body: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_result: Mapped[str | None] = mapped_column(String(20), nullable=True)
