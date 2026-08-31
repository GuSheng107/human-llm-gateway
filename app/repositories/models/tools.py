"""工具沙箱（M12）：tool_whitelist / tool_executions。"""

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
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db import Base
from ...domain.enums import ToolExecutionState
from .base import TimestampMixin, sa_enum, utc_now


class ToolWhitelist(TimestampMixin, Base):
    """管理员维护的服务端工具白名单：命令模板 + 参数 schema。

    command_template 中的 {arg_name} 占位符由用户提交的 arguments
    （必须通过 JSON Schema 校验的字符串值）渲染；非白名单占位符
    在保存时拒绝。
    """

    __tablename__ = "tool_whitelist"
    __table_args__ = (Index("ix_tool_whitelist_enabled", "is_enabled", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    command_template: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ToolExecution(Base):
    """一次工具执行：谁、哪个工具、什么参数、结果与限制命中。

    stdout/stderr 截断保存（上限见 constants）；完整动作进审计。
    """

    __tablename__ = "tool_executions"
    __table_args__ = (
        CheckConstraint(
            "(state = 'succeeded' AND exit_code IS NOT NULL) OR state <> 'succeeded'",
            name="ck_tool_executions_exit_code",
        ),
        Index("ix_tool_executions_user", "user_id", "id"),
        Index("ix_tool_executions_tool", "tool_id", "id"),
        Index("ix_tool_executions_state", "state", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tool_whitelist.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[ToolExecutionState] = mapped_column(sa_enum(ToolExecutionState), nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
