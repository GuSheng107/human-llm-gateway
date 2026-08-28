"""M3 用户治理所需的 API Key 脱敏计数与批量停用。"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..core.time import utc_now
from .models import ApiKey


class ApiKeyRepository:
    def count_enabled_for_user(self, session: Session, user_id: int) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(ApiKey)
                .where(
                    ApiKey.owner_user_id == user_id,
                    ApiKey.is_enabled.is_(True),
                    ApiKey.deleted_at.is_(None),
                )
            )
            or 0
        )

    def disable_all_for_user(self, session: Session, user_id: int) -> int:
        result = session.execute(
            update(ApiKey)
            .where(
                ApiKey.owner_user_id == user_id,
                ApiKey.is_enabled.is_(True),
                ApiKey.deleted_at.is_(None),
            )
            .values(is_enabled=False, updated_at=utc_now())
        )
        return result.rowcount
