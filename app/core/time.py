"""时间工具：统一 naive UTC。"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """返回 naive UTC（SQLite 不保存时区，全部按 UTC 无时区存储与比较）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(value: datetime | None) -> datetime | None:
    """把 API 输入时间统一为数据库使用的 naive UTC。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def iso_utc(value: datetime | None) -> str | None:
    """把数据库 naive UTC 输出为带 Z 的 ISO 8601。"""
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
