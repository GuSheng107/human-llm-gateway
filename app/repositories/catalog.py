"""Fake Model 目录仓库：系统模型、私有模型、模型分组与有效集合查询。

有效集合公式（docs/DATABASE.md §6.3）：
visible = enabled(system ∪ owner_private)
grouped = visible ∩ enabled(group.items)（未绑定分组时为 visible）
effective = grouped ∩ key.selected（未选择时为 grouped）
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..core.time import utc_now
from ..domain.enums import FakeModelScope
from .models import ApiKeyFakeModel, FakeModel, ModelGroup, ModelGroupItem

# 默认系统 Fake Model 种子（全新数据库初始化时写入一次）。
DEFAULT_SYSTEM_MODELS: list[dict] = [
    {"model_id": "human-gateway", "display_name": "Human Gateway", "owned_by": "human-llm-gateway"},
    {
        "model_id": "human-gateway-fast",
        "display_name": "Human Gateway (Fast)",
        "owned_by": "human-llm-gateway",
    },
]


def _now() -> datetime:
    return utc_now()


class FakeModelRepository:
    # ------------------------------------------------------------------
    # Fake Model
    # ------------------------------------------------------------------

    def get(self, session: Session, model_pk: int) -> FakeModel | None:
        row = session.get(FakeModel, model_pk)
        if row is None or row.deleted_at is not None:
            return None
        return row

    def find_system_by_model_id(self, session: Session, model_id: str) -> FakeModel | None:
        return session.execute(
            select(FakeModel).where(
                FakeModel.scope == FakeModelScope.SYSTEM,
                FakeModel.model_id == model_id,
                FakeModel.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

    def find_private_by_model_id(
        self, session: Session, owner_user_id: int, model_id: str
    ) -> FakeModel | None:
        return session.execute(
            select(FakeModel).where(
                FakeModel.scope == FakeModelScope.PRIVATE,
                FakeModel.owner_user_id == owner_user_id,
                FakeModel.model_id == model_id,
                FakeModel.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

    def visible_models(
        self, session: Session, owner_user_id: int, *, only_enabled: bool = True
    ) -> list[FakeModel]:
        """用户可见集合：启用系统模型 ∪ 自己的启用私有模型。

        私有模型与系统模型同 model_id 时，服务层保留私有模型（遮蔽系统模型）。
        """
        rows = list(
            session.scalars(
                select(FakeModel).where(
                    FakeModel.deleted_at.is_(None),
                    or_(
                        FakeModel.owner_user_id.is_(None),  # 系统模型
                        FakeModel.owner_user_id == owner_user_id,  # 自己的私有模型
                    ),
                )
            )
        )
        if only_enabled:
            rows = [row for row in rows if row.is_enabled]
        # 遮蔽：同 model_id 私有模型优先。
        by_model: dict[str, FakeModel] = {}
        for row in rows:
            if row.scope is FakeModelScope.PRIVATE:
                by_model[row.model_id] = row
        result: list[FakeModel] = []
        for row in rows:
            if row.scope is FakeModelScope.SYSTEM and row.model_id in by_model:
                continue
            result.append(row)
        result.sort(key=lambda r: (r.sort_order, r.id))
        return result

    def list_governance(
        self, session: Session, *, page: int, page_size: int, search: str | None = None
    ) -> tuple[list[FakeModel], int]:
        filters = [FakeModel.deleted_at.is_(None)]
        if search:
            term = search.strip()
            filters.append(FakeModel.model_id.ilike(f"%{term}%"))
        total = session.scalar(select(func.count()).select_from(FakeModel).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(FakeModel)
                .where(*filters)
                .order_by(FakeModel.scope, FakeModel.sort_order, FakeModel.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def add(self, session: Session, model: FakeModel) -> FakeModel:
        session.add(model)
        return model

    def soft_delete(self, session: Session, model_pk: int) -> None:
        session.execute(
            update(FakeModel)
            .where(FakeModel.id == model_pk, FakeModel.deleted_at.is_(None))
            .values(deleted_at=_now(), updated_at=_now())
        )

    # ------------------------------------------------------------------
    # 模型分组
    # ------------------------------------------------------------------

    def get_group(self, session: Session, group_pk: int) -> ModelGroup | None:
        row = session.get(ModelGroup, group_pk)
        if row is None or row.deleted_at is not None:
            return None
        return row

    def list_groups(
        self,
        session: Session,
        *,
        owner_user_id: int | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[ModelGroup], int]:
        filters = [ModelGroup.deleted_at.is_(None)]
        if owner_user_id is not None:
            filters.append(ModelGroup.owner_user_id == owner_user_id)
        total = session.scalar(select(func.count()).select_from(ModelGroup).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(ModelGroup)
                .where(*filters)
                .order_by(ModelGroup.created_at.desc(), ModelGroup.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def add_group(self, session: Session, group: ModelGroup) -> ModelGroup:
        session.add(group)
        return group

    def group_items(self, session: Session, group_pk: int) -> list[FakeModel]:
        rows = list(
            session.scalars(
                select(FakeModel)
                .join(ModelGroupItem, ModelGroupItem.fake_model_id == FakeModel.id)
                .where(
                    ModelGroupItem.model_group_id == group_pk,
                    FakeModel.deleted_at.is_(None),
                )
                .order_by(FakeModel.sort_order, FakeModel.id)
            )
        )
        return rows

    def replace_group_items(
        self, session: Session, group_pk: int, fake_model_ids: list[int]
    ) -> None:
        session.query(ModelGroupItem).filter(ModelGroupItem.model_group_id == group_pk).delete()
        for model_pk in dict.fromkeys(fake_model_ids):
            session.add(ModelGroupItem(model_group_id=group_pk, fake_model_id=model_pk))
        session.flush()

    def soft_delete_group(self, session: Session, group_pk: int) -> None:
        session.execute(
            update(ModelGroup)
            .where(ModelGroup.id == group_pk, ModelGroup.deleted_at.is_(None))
            .values(deleted_at=_now(), updated_at=_now())
        )

    def count_enabled_api_key_references_for_group(self, session: Session, group_pk: int) -> int:
        from .models import ApiKey

        return (
            session.scalar(
                select(func.count())
                .select_from(ApiKey)
                .where(
                    ApiKey.model_group_id == group_pk,
                    ApiKey.is_enabled.is_(True),
                    ApiKey.deleted_at.is_(None),
                )
            )
            or 0
        )

    # ------------------------------------------------------------------
    # Key 显式模型集合
    # ------------------------------------------------------------------

    def key_selected_models(self, session: Session, api_key_pk: int) -> list[FakeModel]:
        return list(
            session.scalars(
                select(FakeModel)
                .join(ApiKeyFakeModel, ApiKeyFakeModel.fake_model_id == FakeModel.id)
                .where(ApiKeyFakeModel.api_key_id == api_key_pk)
                .order_by(FakeModel.sort_order, FakeModel.id)
            )
        )

    def replace_key_models(
        self, session: Session, api_key_pk: int, fake_model_ids: list[int]
    ) -> None:
        session.query(ApiKeyFakeModel).filter(ApiKeyFakeModel.api_key_id == api_key_pk).delete()
        for model_pk in dict.fromkeys(fake_model_ids):
            session.add(ApiKeyFakeModel(api_key_id=api_key_pk, fake_model_id=model_pk))
        session.flush()

    # ------------------------------------------------------------------
    # 种子
    # ------------------------------------------------------------------

    def create_system_model(
        self,
        session: Session,
        *,
        model_id: str,
        display_name: str,
        owned_by: str,
        created_by_user_id: int,
    ) -> FakeModel:
        row = FakeModel(
            scope=FakeModelScope.SYSTEM,
            owner_user_id=None,
            model_id=model_id,
            display_name=display_name,
            owned_by=owned_by,
            created_by_user_id=created_by_user_id,
        )
        session.add(row)
        return row

    def seed_default_system_models(self, session: Session, admin_id: int) -> None:
        """幂等写入默认系统模型；已存在的 model_id 不覆盖。"""
        for spec in DEFAULT_SYSTEM_MODELS:
            existing = self.find_system_by_model_id(session, spec["model_id"])
            if existing is None:
                self.create_system_model(
                    session,
                    model_id=spec["model_id"],
                    display_name=spec["display_name"],
                    owned_by=spec["owned_by"],
                    created_by_user_id=admin_id,
                )
