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
#
# 按母公司划分：分组是第一层候选集，模型选择只能在分组内收窄。
# 模型一律为 system scope（owner_user_id 为空），因此对所有用户可见可用；
# 分组挂在管理员名下仅因为 model_groups.owner_user_id 非空约束，不影响模型可见性。
DEFAULT_MODEL_GROUPS: list[dict] = [
    {
        "name": "DeepSeek",
        "owned_by": "deepseek",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
    },
    {
        "name": "OpenAI",
        "owned_by": "openai",
        "models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4"],
    },
    {
        "name": "Claude",
        "owned_by": "claude",
        "models": [
            "claude-fable-5",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-haiku-4-5",
        ],
    },
    {
        "name": "Kimi",
        "owned_by": "kimi",
        "models": ["kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6"],
    },
    {
        "name": "MiniMax",
        "owned_by": "minimax",
        "models": ["MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7-highspeed"],
    },
    {
        "name": "Grok",
        "owned_by": "grok",
        "models": ["grok-4.6", "grok-4.5", "grok-4.3"],
    },
    {
        "name": "Qwen",
        "owned_by": "qwen",
        "models": [
            "qwen3.8-max",
            "qwen3.8-flash",
            "qwen3.7-max",
            "qwen3.7-plus",
            "qwen3.7-flash",
        ],
    },
    {
        "name": "GLM",
        "owned_by": "glm",
        "models": ["glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-4.7"],
    },
    {
        "name": "Gemini",
        "owned_by": "gemini",
        "models": [
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
        ],
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

    def find_group_by_name(
        self, session: Session, owner_user_id: int, name: str
    ) -> ModelGroup | None:
        return session.execute(
            select(ModelGroup).where(
                ModelGroup.owner_user_id == owner_user_id,
                ModelGroup.name == name,
                ModelGroup.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

    def list_groups(
        self,
        session: Session,
        *,
        owner_user_id: int | None = None,
        include_public: bool = False,
        page: int,
        page_size: int,
    ) -> tuple[list[ModelGroup], int]:
        filters = [ModelGroup.deleted_at.is_(None)]
        if owner_user_id is not None:
            if include_public:
                filters.append(
                    or_(
                        ModelGroup.owner_user_id == owner_user_id,
                        ModelGroup.is_public.is_(True),
                    )
                )
            else:
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
        """幂等写入默认系统模型与按母公司划分的分组。

        模型为 system scope（对所有用户可见可用）；分组挂在管理员名下，
        仅因为 owner_user_id 非空约束。已存在的模型/分组不覆盖，分组内
        成员仅在分组为空时写入，避免重启冲掉管理员的调整。
        """
        for spec in DEFAULT_MODEL_GROUPS:
            group = self.find_group_by_name(session, admin_id, spec["name"])
            if group is None:
                group = ModelGroup(owner_user_id=admin_id, name=spec["name"], is_public=True)
                self.add_group(session, group)
                session.flush()
            model_pks: list[int] = []
            for model_id in spec["models"]:
                model = self.find_system_by_model_id(session, model_id)
                if model is None:
                    model = self.create_system_model(
                        session,
                        model_id=model_id,
                        display_name=model_id,
                        owned_by=spec["owned_by"],
                        created_by_user_id=admin_id,
                    )
                    session.flush()
                model_pks.append(model.id)
            if not self.group_items(session, group.id):
                self.replace_group_items(session, group.id, model_pks)
