from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..models import SystemSetting

SETTING_DEFAULTS: dict[str, Any] = {
    "human_timeout_seconds": 300,
    "binding_code_ttl_seconds": 300,
    "binding_max_attempts": 5,
    "circuit_breaker_threshold": 5,
    "circuit_breaker_cooldown_seconds": 60,
    "allow_plain_human_reply": True,
    "stream_chunk_size": 24,
    "stream_delay_min_ms": 20,
    "stream_delay_max_ms": 90,
}

WRITABLE_KEYS: set[str] = set(SETTING_DEFAULTS.keys())


def get_setting(db: Session, key: str) -> Any:
    row = db.get(SystemSetting, key)
    if row is None:
        return SETTING_DEFAULTS.get(key)
    return _coerce(key, row.value)


def get_settings_overrides(db: Session) -> dict[str, Any]:
    return {key: get_setting(db, key) for key in SETTING_DEFAULTS}


def set_setting(db: Session, key: str, value: Any) -> None:
    if key not in WRITABLE_KEYS:
        raise KeyError(key)
    stored = json.dumps(value, ensure_ascii=False)
    row = db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=stored))
    else:
        row.value = stored
    db.commit()


def runtime_settings() -> dict[str, Any]:
    """读取 .env 基线叠加 DB 覆盖，返回当前生效运行参数。"""
    base = get_settings()
    with SessionLocal() as db:
        overrides = get_settings_overrides(db)
    return {
        "human_timeout_seconds": overrides["human_timeout_seconds"],
        "binding_code_ttl_seconds": overrides["binding_code_ttl_seconds"],
        "binding_max_attempts": overrides["binding_max_attempts"],
        "circuit_breaker_threshold": overrides["circuit_breaker_threshold"],
        "circuit_breaker_cooldown_seconds": overrides["circuit_breaker_cooldown_seconds"],
        "allow_plain_human_reply": overrides["allow_plain_human_reply"],
        "stream_chunk_size": overrides["stream_chunk_size"],
        "stream_delay_min_ms": overrides["stream_delay_min_ms"],
        "stream_delay_max_ms": overrides["stream_delay_max_ms"],
        "app_secret": base.app_secret,
    }


def _coerce(key: str, raw: str) -> Any:
    default = SETTING_DEFAULTS[key]
    if isinstance(default, bool):
        return raw.lower() in ("true", "1", "yes")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    return raw
