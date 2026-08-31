"""LLM 配置仓库：所有权查询、分页列表、引用计数与物理删除（docs/DATABASE.md §4.4）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.time import utc_now
from .models import LlmConfig


def _now() -> datetime:
    return utc_now()


class LlmConfigRepository:
    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, session: Session, config_id: int) -> LlmConfig | None:
        return session.get(LlmConfig, config_id)

    def get_owned(self, session: Session, config_id: int, owner_user_id: int) -> LlmConfig | None:
        row = self.get(session, config_id)
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
    ) -> tuple[list[LlmConfig], int]:
        filters: list = []
        if owner_user_id is not None:
            filters.append(LlmConfig.owner_user_id == owner_user_id)
        if search:
            term = search.strip()
            filters.append(
                or_(
                    LlmConfig.name.ilike(f"%{term}%"),
                    LlmConfig.real_model.ilike(f"%{term}%"),
                    LlmConfig.base_url.ilike(f"%{term}%"),
                )
            )
        total = session.scalar(select(func.count()).select_from(LlmConfig).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(LlmConfig)
                .where(*filters)
                .order_by(LlmConfig.created_at.desc(), LlmConfig.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def get_by_name(self, session: Session, *, owner_user_id: int, name: str) -> LlmConfig | None:
        """按 owner + name 唯一索引定位（用于重名校验）。"""
        return session.execute(
            select(LlmConfig).where(
                LlmConfig.owner_user_id == owner_user_id,
                LlmConfig.name == name,
            )
        ).scalar_one_or_none()

    def count_api_key_references(self, session: Session, *, config_id: int) -> int:
        """引用该配置的 API Key 数。"""
        from .models import ApiKey

        return (
            session.scalar(
                select(func.count())
                .select_from(ApiKey)
                .where(
                    ApiKey.llm_config_id == config_id,
                )
            )
            or 0
        )

    def count_active_task_references(self, session: Session, *, config_id: int) -> int:
        """引用该配置的活动任务数（slot_released_at IS NULL）。"""
        from ..domain.tasks import TERMINAL_STATES
        from .models import RequestTask

        return (
            session.scalar(
                select(func.count())
                .select_from(RequestTask)
                .where(
                    RequestTask.llm_config_id_snapshot == config_id,
                    RequestTask.slot_released_at.is_(None),
                    RequestTask.state.not_in(list(TERMINAL_STATES)),
                )
            )
            or 0
        )

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def add(self, session: Session, row: LlmConfig) -> LlmConfig:
        session.add(row)
        return row

    def delete(self, session: Session, config_id: int) -> bool:
        row = session.get(LlmConfig, config_id)
        if row is None:
            return False
        session.delete(row)
        return True

    def record_test_result(
        self,
        session: Session,
        *,
        config_id: int,
        result: str,
    ) -> None:
        from sqlalchemy import update as sa_update

        session.execute(
            sa_update(LlmConfig)
            .where(LlmConfig.id == config_id)
            .values(last_tested_at=_now(), last_test_result=result, updated_at=_now())
        )
