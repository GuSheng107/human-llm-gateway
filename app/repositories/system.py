"""设置、审计与日志仓库。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..core.logging import get_request_id, sanitize_log_value
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
            request_id=request_id or get_request_id(),
            metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
        )
        session.add(row)
        return row

    def list_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        actor_user_id: int | None = None,
        resource_type: str | None = None,
        action: str | None = None,
        owner_user_id: int | None = None,
        request_id: str | None = None,
        hours: int | None = None,
    ) -> tuple[list[AuditLog], int]:
        """管理员审计检索：按操作者/资源/动作/所有者/traceId/时间窗筛选。"""
        from sqlalchemy import func, select

        filters: list[Any] = []
        if actor_user_id is not None:
            filters.append(AuditLog.actor_user_id == actor_user_id)
        if resource_type:
            filters.append(AuditLog.resource_type == resource_type)
        if action:
            filters.append(AuditLog.action == action)
        if owner_user_id is not None:
            filters.append(AuditLog.owner_user_id == owner_user_id)
        if request_id:
            filters.append(AuditLog.request_id == request_id)
        if hours is not None and hours > 0:
            from datetime import timedelta

            filters.append(AuditLog.created_at >= _now() - timedelta(hours=hours))
        total = session.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(AuditLog)
                .where(*filters)
                .order_by(AuditLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def list_for_subject(
        self,
        session: Session,
        *,
        subject_user_id: int,
        limit: int,
        hours: int | None = None,
        request_id: str | None = None,
        action: str | None = None,
    ) -> list[AuditLog]:
        """本人可见审计：actor 或 owner 是自己。仅返回用于合并视图的条数。"""
        from datetime import timedelta

        from sqlalchemy import or_, select

        filters: list[Any] = [
            or_(
                AuditLog.actor_user_id == subject_user_id,
                AuditLog.owner_user_id == subject_user_id,
            )
        ]
        if hours is not None and hours > 0:
            filters.append(AuditLog.created_at >= _now() - timedelta(hours=hours))
        if request_id:
            filters.append(AuditLog.request_id == request_id)
        if action:
            filters.append(AuditLog.action == action)
        rows = list(
            session.scalars(
                select(AuditLog).where(*filters).order_by(AuditLog.id.desc()).limit(limit)
            )
        )
        return rows


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
            request_id=request_id or get_request_id(),
            user_id=user_id,
            task_id=task_id,
            api_key_id=api_key_id,
            connection_id=connection_id,
            context_json=(
                json.dumps(sanitize_log_value(context), ensure_ascii=False) if context else None
            ),
        )
        session.add(row)
        return row

    def list_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        level: str | None = None,
        event: str | None = None,
        user_id: int | None = None,
        task_id: int | None = None,
        api_key_id: int | None = None,
        connection_id: int | None = None,
        request_id: str | None = None,
        scope_owner_id: int | None = None,
        hours: int | None = None,
    ) -> tuple[list[AppLog], int]:
        """应用日志检索：按级别/事件/关联 ID/request_id/时间窗筛选。"""
        from sqlalchemy import func, select

        filters: list[Any] = []
        if level:
            filters.append(AppLog.level == level)
        if event:
            filters.append(AppLog.event == event)
        if request_id:
            filters.append(AppLog.request_id == request_id)
        if scope_owner_id is not None:
            # 普通用户数据范围：自身标记的日志 + 归属自己的 Key / 连接 / 任务。
            from sqlalchemy import or_

            from .models import ApiKey, ImConnection, RequestTask

            owned_key_ids = select(ApiKey.id).where(ApiKey.owner_user_id == scope_owner_id)
            owned_conn_ids = select(ImConnection.id).where(
                ImConnection.owner_user_id == scope_owner_id
            )
            owned_task_ids = select(RequestTask.id).where(RequestTask.api_key_id.in_(owned_key_ids))
            filters.append(
                or_(
                    AppLog.user_id == scope_owner_id,
                    AppLog.api_key_id.in_(owned_key_ids),
                    AppLog.connection_id.in_(owned_conn_ids),
                    AppLog.task_id.in_(owned_task_ids),
                )
            )
        if user_id is not None:
            filters.append(AppLog.user_id == user_id)
        if task_id is not None:
            filters.append(AppLog.task_id == task_id)
        if api_key_id is not None:
            filters.append(AppLog.api_key_id == api_key_id)
        if connection_id is not None:
            filters.append(AppLog.connection_id == connection_id)
        if hours is not None and hours > 0:
            from datetime import timedelta

            filters.append(AppLog.created_at >= _now() - timedelta(hours=hours))
        total = session.scalar(select(func.count()).select_from(AppLog).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(AppLog)
                .where(*filters)
                .order_by(AppLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total
