from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AdminUser, AuditLog
from ..services.settings_service import (
    SETTING_DEFAULTS,
    get_settings_overrides,
    set_setting,
)
from .deps import require_admin
from .errors import ApiError, ErrorCode

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def read_settings(
    admin: AdminUser = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return {"items": get_settings_overrides(db), "keys": sorted(SETTING_DEFAULTS.keys())}


@router.post("")
def update_settings(
    payload: dict[str, Any] = Body(default={}),
    admin: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    import json

    changed: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in SETTING_DEFAULTS:
            raise ApiError(ErrorCode.VALIDATION_FAILED, f"不可写的设置项：{key}")
        expected = SETTING_DEFAULTS[key]
        if isinstance(expected, bool) and not isinstance(value, bool):
            raise ApiError(ErrorCode.VALIDATION_FAILED, f"{key} 必须是布尔值")
        if isinstance(expected, int) and not isinstance(value, int):
            raise ApiError(ErrorCode.VALIDATION_FAILED, f"{key} 必须是整数")
        set_setting(db, key, value)
        changed[key] = value
    if changed:
        db.add(
            AuditLog(
                action="settings.updated",
                subject_type="system_settings",
                subject_id="runtime",
                actor=admin.username,
                detail_json=json.dumps(changed, ensure_ascii=False),
            )
        )
        db.commit()
    return {"updated": changed, "items": get_settings_overrides(db)}
