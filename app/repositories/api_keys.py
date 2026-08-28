"""API Key 仓库：鉴权查找、所有权查询与生命周期操作。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..core.security import verify_api_key
from ..core.time import utc_now
from .models import ApiKey


def _now() -> datetime:
    return utc_now()


class ApiKeyRepository:
    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, session: Session, key_pk: int) -> ApiKey | None:
        row = session.get(ApiKey, key_pk)
        if row is None or row.deleted_at is not None:
            return None
        return row

    def get_owned(self, session: Session, key_pk: int, owner_user_id: int) -> ApiKey | None:
        row = self.get(session, key_pk)
        if row is None or row.owner_user_id != owner_user_id:
            return None
        return row

    def list_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        owner_user_id: int | None = None,
        search: str | None = None,
    ) -> tuple[list[ApiKey], int]:
        filters: list = [ApiKey.deleted_at.is_(None)]
        if owner_user_id is not None:
            filters.append(ApiKey.owner_user_id == owner_user_id)
        if search:
            term = search.strip()
            filters.append(
                or_(ApiKey.name.ilike(f"%{term}%"), ApiKey.key_prefix.ilike(f"%{term}%"))
            )
        total = session.scalar(select(func.count()).select_from(ApiKey).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(ApiKey)
                .where(*filters)
                .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def find_by_prefix(self, session: Session, key_prefix: str) -> list[ApiKey]:
        """按前缀缩小鉴权候选集（明文只存盐化哈希，无法直接反查）。"""
        return list(
            session.scalars(
                select(ApiKey).where(
                    ApiKey.key_prefix == key_prefix,
                    ApiKey.deleted_at.is_(None),
                )
            )
        )

    def authenticate(self, session: Session, plaintext: str) -> ApiKey | None:
        """验证明文 Key；要求 Key 启用且所有者仍处于启用状态。"""
        from .models import User

        prefix = plaintext[:12]
        for row in self.find_by_prefix(session, prefix):
            if not verify_api_key(plaintext, row.key_hash):
                continue
            owner = session.get(User, row.owner_user_id)
            if owner is None or not owner.is_active:
                return None
            return row
        return None

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def add(self, session: Session, key: ApiKey) -> ApiKey:
        session.add(key)
        return key

    def touch_last_used(self, session: Session, key_pk: int) -> None:
        session.execute(update(ApiKey).where(ApiKey.id == key_pk).values(last_used_at=_now()))

    def set_enabled(self, session: Session, key_pk: int, enabled: bool) -> None:
        session.execute(
            update(ApiKey).where(ApiKey.id == key_pk).values(is_enabled=enabled, updated_at=_now())
        )

    def soft_delete(self, session: Session, key_pk: int) -> None:
        session.execute(
            update(ApiKey)
            .where(ApiKey.id == key_pk, ApiKey.deleted_at.is_(None))
            .values(is_enabled=False, deleted_at=_now(), updated_at=_now())
        )

    # ------------------------------------------------------------------
    # M3 治理支持（保留既有接口）
    # ------------------------------------------------------------------

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
            .values(is_enabled=False, updated_at=_now())
        )
        return result.rowcount
