"""请求级上下文（request_id）与结构化日志。"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar, Token
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()


_STRUCTURED = logging.getLogger("human_llm_gateway")

_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set_cookie",
        "password",
        "passwd",
        "credentials",
        "credential",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "session_token",
        "client_secret",
        "secret",
        "password_hash",
        "token_hash",
        "key_hash",
        "code_hash",
        "config_ciphertext",
        "secret_ciphertext",
        "invitation_code",
        "binding_code",
        "temporary_password",
        "avatar_base64",
        "code",
    }
)
_SENSITIVE_SUFFIXES = (
    "_password",
    "_passwd",
    "_secret",
    "_token",
    "_cookie",
    "_credential",
    "_credentials",
)
_REDACTED = "[REDACTED]"


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_EXACT_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES):
        return True
    return "api_key" in normalized and not normalized.endswith(("_id", "_prefix"))


def sanitize_log_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_sensitive_key(key) else sanitize_log_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_log_value(item) for item in value]
    return value


def sanitize_log_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return sanitize_log_value(fields)


def _configure_logging() -> None:
    if _STRUCTURED.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _STRUCTURED.addHandler(handler)
    _STRUCTURED.setLevel(logging.INFO)


def log_event(level: str, event: str, message: str, **fields: object) -> None:
    """结构化日志；resource ID 等关联字段以关键字传入，缺失省略。

    除输出到 stdout 外同时尽力持久化到 ``app_logs``（独立短会话提交，
    不影响调用方事务；持久化失败静默降级为仅控制台输出）。
    """
    _configure_logging()
    record: dict[str, object] = {
        "level": level,
        "event": event,
        "message": message,
    }
    request_id = get_request_id()
    if request_id is not None:
        record["request_id"] = request_id
    record.update(sanitize_log_fields(fields))
    _STRUCTURED.log(
        getattr(logging, level.upper(), logging.INFO),
        "%s",
        json.dumps(record, ensure_ascii=False, default=str),
    )
    _persist_log(level, event, message, request_id, record)


def _persist_log(
    level: str,
    event: str,
    message: str,
    request_id: str | None,
    record: dict[str, object],
) -> None:
    """把结构化日志落入 app_logs；任何失败都不影响主流程。"""
    try:
        from sqlalchemy import text

        from .db import SessionLocal

        def _int(key: str) -> int | None:
            value = record.get(key)
            if isinstance(value, bool) or value is None:
                return None
            try:
                return int(str(value))
            except (TypeError, ValueError):
                return None

        context = {k: v for k, v in record.items() if k not in {"level", "event", "message"}}
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO app_logs (level, event, message, request_id, user_id, task_id,"
                    " api_key_id, connection_id, context_json, created_at)"
                    " VALUES (:level, :event, :message, :request_id, :user_id, :task_id,"
                    " :api_key_id, :connection_id, :context, CURRENT_TIMESTAMP)"
                ),
                {
                    "level": level,
                    "event": event,
                    "message": message,
                    "request_id": request_id,
                    "user_id": _int("user_id"),
                    "task_id": _int("task_id"),
                    "api_key_id": _int("api_key_id"),
                    "connection_id": _int("connection_id"),
                    "context": json.dumps(context, ensure_ascii=False, default=str),
                },
            )
            session.commit()
    except Exception:  # noqa: BLE001 - 日志持久化失败绝不影响业务主链路
        if _STRUCTURED.isEnabledFor(logging.DEBUG):
            _STRUCTURED.debug("app log persist failed", exc_info=True)
