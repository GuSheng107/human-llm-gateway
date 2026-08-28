from __future__ import annotations

import json
from typing import Any

from .config import get_settings
from .security import decrypt_secret, encrypt_secret

CONFIG_PREFIX = "enc:v1:"


def dump_connection_config(config: dict[str, Any]) -> str:
    """Encrypt an IM connector configuration as one authenticated payload."""
    raw = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    encrypted = encrypt_secret(raw, get_settings().app_secret)
    return f"{CONFIG_PREFIX}{encrypted}"


def load_connection_config(value: str | None) -> dict[str, Any]:
    """Decrypt a connector configuration written by this version of the service."""
    raw = value or ""
    if not raw.startswith(CONFIG_PREFIX):
        raise ValueError("IM 连接配置格式无效")
    raw = decrypt_secret(raw.removeprefix(CONFIG_PREFIX), get_settings().app_secret)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError("IM 连接配置必须是 JSON 对象")
    return parsed
