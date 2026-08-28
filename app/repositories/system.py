"""设置、审计与日志仓库。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..core.time import utc_now
from ..domain.enums import AuditAction, AuditResult
from .models import AppLog, AuditLog, SystemSetting


def _now() -> datetime:
    return utc_now()


class SystemSettingRepository:
    def get(self, session: Session, key: str) -> str | None:
        row = session.get(SystemSetting, key)
        return row.value_json if row else None

    def get_json(self, session: Session, key: str) -> Any | None:
        value = self.get(session, key)
        return json.loads(value) if value is not None else None

    def set(
        self,
        session: Session,
        key: str,
        value: Any,
        *,
        updated_by_user_id: int | None = None,
    ) -> SystemSetting:
        row = session.get(SystemSetting, key)
        encoded = json.dumps(value, ensure_ascii=False)
        if row is None:
            row = SystemSetting(key=key, value_json=encoded, updated_by_user_id=updated_by_user_id)
            session.add(row)
        else:
            row.value_json = encoded
            row.updated_by_user_id = updated_by_user_id
            row.updated_at = _now()
        return row


class AuditRepository:
    def add(
        self,
        session: Session,
        *,
        action: AuditAction,
        resource_type: str,
        result: AuditResult = AuditResult.SUCCESS,
        actor_user_id: int | None = None,
        resource_id: str | None = None,
        owner_user_id: int | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        row = AuditLog(
            actor_user_id=actor_user_id,
            action=action.value,
            resource_type=resource_type,
            resource_id=resource_id,
            owner_user_id=owner_user_id,
            result=result,
            request_id=request_id,
            metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
        )
        session.add(row)
        return row


class AppLogRepository:
    def add(
        self,
        session: Session,
        *,
        level: str = "info",
        event: str = "",
        message: str = "",
        request_id: str | None = None,
        user_id: int | None = None,
        task_id: int | None = None,
        api_key_id: int | None = None,
        connection_id: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> AppLog:
        row = AppLog(
            level=level,
            event=event,
            message=message,
            request_id=request_id,
            user_id=user_id,
            task_id=task_id,
            api_key_id=api_key_id,
            connection_id=connection_id,
            context_json=json.dumps(context, ensure_ascii=False) if context else None,
        )
        session.add(row)
        return row
