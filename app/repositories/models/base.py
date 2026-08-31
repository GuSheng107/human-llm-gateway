"""模型公共基类与列工具。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ...core.time import utc_now  # 重新导出供模型列默认值使用


def sa_enum(enum_cls):
    """以稳定小写字符串值（而非成员名）持久化枚举。"""
    return Enum(enum_cls, values_callable=lambda e: [m.value for m in e], native_enum=False)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class VersionMixin:
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
