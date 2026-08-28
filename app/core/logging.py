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
        "headers_ciphertext",
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
    record.update(sanitize_log_fields(fields))
    _STRUCTURED.log(
        getattr(logging, level.upper(), logging.INFO),
        "%s",
        json.dumps(record, ensure_ascii=False, default=str),
    )
