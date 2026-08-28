"""统一运行日志：标准字段、数据库落库和可见的降级输出。"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from . import db as db_module
from .models import AppLog

LEVELS = {"debug", "info", "warning", "error", "critical"}


def log_event(
    level: str,
    logger: str,
    message: str,
    detail: dict[str, Any] | None = None,
    *,
    event: str | None = None,
) -> None:
    """写入规范化运行事件；数据库故障时输出结构化 stderr，绝不静默丢失。"""

    normalized_level = level.strip().lower()
    if normalized_level not in LEVELS:
        normalized_level = "info"
    category = logger.strip().lower() or "app"
    context = dict(detail or {})
    context.setdefault("event", event or f"{category}.event")
    context.setdefault("timestamp", datetime.now(UTC).isoformat())
    try:
        with db_module.SessionLocal() as db:
            db.add(
                AppLog(
                    level=normalized_level,
                    logger=category,
                    message=message,
                    detail_json=json.dumps(context, ensure_ascii=False, default=str),
                )
            )
            db.commit()
    except Exception as exc:  # noqa: BLE001 - 日志边界必须吞掉存储故障，但不可静默
        fallback = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": normalized_level,
            "logger": category,
            "message": message,
            "detail": context,
            "log_storage_error": str(exc),
        }
        sys.stderr.write(json.dumps(fallback, ensure_ascii=False, default=str) + "\n")


class DBLogHandler(logging.Handler):
    """把 WARNING 及以上标准日志桥接到同一 AppLog 结构。"""

    marker = "human_llm_db_handler"

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        detail: dict[str, Any] = {
            "event": "python.logging",
            "function": record.funcName,
            "line": record.lineno,
            "module": record.module,
        }
        if record.exc_info:
            detail["exception"] = self.format(record)
        log_event(
            record.levelname.lower(),
            record.name,
            record.getMessage(),
            detail,
        )


def install_db_log_handler() -> DBLogHandler:
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "marker", "") == DBLogHandler.marker:
            return handler  # type: ignore[return-value]
    handler = DBLogHandler()
    root.addHandler(handler)
    return handler


def remove_db_log_handler(handler: DBLogHandler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()
