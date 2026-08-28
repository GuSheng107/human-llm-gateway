"""请求级上下文（request_id）与结构化日志。"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


_STRUCTURED = logging.getLogger("human_llm_gateway")


def _configure_logging() -> None:
    if _STRUCTURED.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _STRUCTURED.addHandler(handler)
    _STRUCTURED.setLevel(logging.INFO)


def log_event(level: str, event: str, message: str, **fields: object) -> None:
    """结构化日志；resource ID 等关联字段以关键字传入，缺失省略。"""
    _configure_logging()
    record: dict[str, object] = {
        "level": level,
        "event": event,
        "message": message,
    }
    request_id = get_request_id()
    if request_id is not None:
        record["request_id"] = request_id
    record.update(fields)
    _STRUCTURED.log(getattr(logging, level.upper(), logging.INFO), "%s", record)
