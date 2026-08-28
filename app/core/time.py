"""时间工具：统一 naive UTC。"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """返回 naive UTC（SQLite 不保存时区，全部按 UTC 无时区存储与比较）。"""
    return datetime.now(UTC).replace(tzinfo=None)
