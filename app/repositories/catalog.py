"""Fake Model 目录仓库：系统模型、私有模型、模型分组与有效集合查询。

有效集合公式（docs/DATABASE.md §6.3）：
visible = enabled(system ∪ owner_private)
grouped = visible ∩ enabled(group.items)（未绑定分组时为 visible）
effective = grouped ∩ key.selected（未选择时为 grouped）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.time import utc_now
from ..domain.enums import BillingTier, FakeModelScope, ModelEndpointType
from .models import ApiKeyFakeModel, FakeModel, ModelGroup, ModelGroupItem

# 默认系统 Fake Model 种子（全新数据库初始化时写入一次）。
#
# 按母公司划分：分组是第一层候选集，模型选择只能在分组内收窄。
# 模型一律为 system scope（owner_user_id 为空），因此对所有用户可见可用；
# 分组挂在管理员名下仅因为 model_groups.owner_user_id 非空约束，不影响模型可见性。
# meta：模型广场展示属性（价格单位：元 / 1M tokens；capabilities/tags 为标签）。
DEFAULT_MODEL_GROUPS: list[dict] = [
    {
        "name": "DeepSeek",
        "owned_by": "deepseek",
        "models": {
            "deepseek-v4-pro": {
                "input": 4,
                "output": 16,
                "cached": 0.4,
                "context": 1_000_000,
                "max_output": 32_000,
                "capabilities": ["tools", "thinking", "streaming"],
                "tags": ["代码", "推理"],
            },
            "deepseek-v4-flash": {
                "input": 0.5,
                "output": 2,
                "cached": 0.05,
                "context": 1_000_000,
                "max_output": 16_000,
                "capabilities": ["tools", "thinking", "streaming"],
                "tags": ["性价比"],
            },
        },
    },
    {
        "name": "OpenAI",
        "owned_by": "openai",
        "models": {
            "gpt-5.6-sol": {
                "input": 4,
                "output": 20,
                "cached": 0.4,
                "context": 1_050_000,
                "max_output": 128_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "endpoints": ["openai_chat", "openai_responses"],
                "tags": ["旗舰", "多模态"],
            },
            "gpt-5.6-terra": {
                "input": 2,
                "output": 12,
                "cached": 0.2,
                "context": 1_050_000,
                "max_output": 128_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "endpoints": ["openai_chat", "openai_responses"],
                "tags": ["均衡"],
            },
            "gpt-5.6-luna": {
                "input": 0.2,
                "output": 1.2,
                "cached": 0.02,
                "context": 1_050_000,
                "max_output": 128_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "endpoints": ["openai_chat", "openai_responses"],
                "tags": ["轻量"],
            },
            "gpt-5.5": {
                "input": 5,
                "output": 30,
                "cached": 0.5,
                "context": 1_050_000,
                "max_output": 128_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "endpoints": ["openai_chat", "openai_responses"],
                "tags": [],
            },
            "gpt-5.4": {
                "input": 2.5,
                "output": 15,
                "cached": 0.25,
                "context": 1_050_000,
                "max_output": 128_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "endpoints": ["openai_chat", "openai_responses"],
                "tags": ["入门"],
            },
        },
    },
    {
        "name": "Claude",
        "owned_by": "claude",
        "models": {
            "claude-fable-5": {
                "input": 30,
                "output": 120,
                "cached": 3,
                "cached_write": 37.5,
                "context": 1_000_000,
                "max_output": 128_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "endpoints": ["anthropic_messages"],
                "tags": ["旗舰", "长文本"],
            },
            "claude-opus-5": {
                "input": 21,
                "output": 105,
                "cached": 2.1,
                "cached_write": 26.25,
                "context": 1_000_000,
                "max_output": 128_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "endpoints": ["anthropic_messages"],
                "tags": ["推理"],
            },
            "claude-sonnet-5": {
                "input": 10.5,
                "output": 42,
                "cached": 1.05,
                "cached_write": 13.1,
                "context": 1_000_000,
                "max_output": 128_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "endpoints": ["anthropic_messages"],
                "tags": ["均衡"],
            },
            "claude-haiku-4-5": {
                "input": 5,
                "output": 20,
                "cached": 0.5,
                "cached_write": 6.25,
                "context": 200_000,
                "max_output": 32_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "endpoints": ["anthropic_messages"],
                "tags": ["轻量"],
            },
        },
    },
    {
        "name": "Kimi",
        "owned_by": "kimi",
        "models": {
            "kimi-k3": {
                "input": 8,
                "output": 32,
                "context": 1_000_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["长文本"],
            },
            "kimi-k2.7-code": {
                "input": 4,
                "output": 16,
                "context": 256_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["代码"],
            },
            "kimi-k2.7-code-highspeed": {
                "input": 8,
                "output": 32,
                "context": 256_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["代码", "高速"],
            },
            "kimi-k2.6": {
                "input": 2,
                "output": 8,
                "context": 256_000,
                "max_output": 32_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["性价比"],
            },
        },
    },
    {
        "name": "MiniMax",
        "owned_by": "minimax",
        "models": {
            "MiniMax-M3": {
                "input": 3,
                "output": 12,
                "context": 1_000_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["推理"],
            },
            "MiniMax-M2.7": {
                "input": 1.5,
                "output": 6,
                "context": 204_800,
                "max_output": 32_000,
                "capabilities": ["tools", "thinking", "streaming"],
                "tags": ["性价比"],
            },
            "MiniMax-M2.7-highspeed": {
                "input": 3,
                "output": 12,
                "context": 204_800,
                "max_output": 32_000,
                "capabilities": ["tools", "thinking", "streaming"],
                "tags": ["高速"],
            },
        },
    },
    {
        "name": "Grok",
        "owned_by": "grok",
        "models": {
            "grok-4.6": {
                "input": 18,
                "output": 72,
                "context": 500_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["旗舰"],
            },
            "grok-4.5": {
                "input": 9,
                "output": 36,
                "context": 500_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["均衡"],
            },
            "grok-4.3": {
                "input": 4.5,
                "output": 18,
                "context": 1_000_000,
                "max_output": 32_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["轻量"],
            },
        },
    },
    {
        "name": "Qwen",
        "owned_by": "qwen",
        "models": {
            "qwen3.8-max": {
                "input": 6,
                "output": 24,
                "context": 1_000_000,
                "max_output": 131_072,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["旗舰"],
            },
            "qwen3.8-flash": {
                "input": 1,
                "output": 4,
                "context": 1_000_000,
                "max_output": 131_072,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["性价比"],
            },
            "qwen3.7-max": {
                "input": 4.8,
                "output": 19.2,
                "context": 1_000_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "streaming"],
                "tags": [],
            },
            "qwen3.7-plus": {
                "input": 1.6,
                "output": 6.4,
                "context": 1_000_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "streaming"],
                "tags": [],
            },
            "qwen3.7-flash": {
                "input": 0.4,
                "output": 1.6,
                "context": 1_000_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "streaming"],
                "tags": ["入门"],
            },
        },
    },
    {
        "name": "GLM",
        "owned_by": "glm",
        "models": {
            "glm-5.3": {
                "input": 3.5,
                "output": 14,
                "context": 1_000_000,
                "max_output": 64_000,
                "capabilities": ["tools", "thinking", "streaming"],
                "tags": ["代码", "推理"],
            },
            "glm-5.3-flash": {
                "input": 0.7,
                "output": 2.8,
                "context": 1_000_000,
                "max_output": 128_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["免费试用"],
            },
            "glm-5.2": {
                "input": 2,
                "output": 8,
                "context": 1_000_000,
                "max_output": 128_000,
                "capabilities": ["tools", "thinking", "streaming"],
                "tags": [],
            },
            "glm-4.7": {
                "input": 1,
                "output": 4,
                "context": 200_000,
                "max_output": 16_000,
                "capabilities": ["tools", "thinking", "streaming"],
                "tags": ["入门"],
            },
        },
    },
    {
        "name": "Gemini",
        "owned_by": "gemini",
        "models": {
            "gemini-3.7-flash": {
                "input": 2.1,
                "output": 8.4,
                "cached": 0.5,
                "context": 1_000_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["长文本"],
            },
            "gemini-3.6-flash": {
                "input": 1.75,
                "output": 7,
                "cached": 0.44,
                "context": 1_000_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["性价比"],
            },
            "gemini-3.5-flash": {
                "input": 1,
                "output": 4,
                "cached": 0.25,
                "context": 1_000_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["性价比"],
            },
            "gemini-3.5-flash-lite": {
                "input": 0.35,
                "output": 1.4,
                "cached": 0.09,
                "context": 1_000_000,
                "max_output": 64_000,
                "capabilities": ["vision", "tools", "thinking", "streaming"],
                "tags": ["入门"],
            },
        },
    },
]


def _now() -> datetime:
    return utc_now()


class FakeModelRepository:
    # ------------------------------------------------------------------
    # Fake Model
    # ------------------------------------------------------------------

    def get(self, session: Session, model_pk: int) -> FakeModel | None:
        return session.get(FakeModel, model_pk)

    def find_system_by_model_id(self, session: Session, model_id: str) -> FakeModel | None:
        return session.execute(
            select(FakeModel).where(
                FakeModel.scope == FakeModelScope.SYSTEM,
                FakeModel.model_id == model_id,
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

    def list_all_governance(self, session: Session) -> list[FakeModel]:
        """治理列表全量行（搜索与多维筛选由服务层统一完成）。"""
        return list(
            session.scalars(
                select(FakeModel).order_by(FakeModel.scope, FakeModel.sort_order, FakeModel.id)
            )
        )

    def list_governance(
        self, session: Session, *, page: int, page_size: int, search: str | None = None
    ) -> tuple[list[FakeModel], int]:
        filters = []
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

    def delete(self, session: Session, model_pk: int) -> None:
        row = session.get(FakeModel, model_pk)
        if row is not None:
            session.delete(row)

    # ------------------------------------------------------------------
    # 模型分组
    # ------------------------------------------------------------------

    def get_group(self, session: Session, group_pk: int) -> ModelGroup | None:
        return session.get(ModelGroup, group_pk)

    def find_group_by_name(
        self, session: Session, owner_user_id: int, name: str
    ) -> ModelGroup | None:
        return session.execute(
            select(ModelGroup).where(
                ModelGroup.owner_user_id == owner_user_id,
                ModelGroup.name == name,
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
        filters = []
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

    def delete_group(self, session: Session, group_pk: int) -> None:
        row = session.get(ModelGroup, group_pk)
        if row is not None:
            session.delete(row)

    def count_enabled_api_key_references_for_group(self, session: Session, group_pk: int) -> int:
        from .models import ApiKey

        return (
            session.scalar(
                select(func.count())
                .select_from(ApiKey)
                .where(
                    ApiKey.model_group_id == group_pk,
                    ApiKey.is_enabled.is_(True),
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
        description: str | None = None,
        input_price_per_million: Decimal | None = None,
        output_price_per_million: Decimal | None = None,
        cached_input_price_per_million: Decimal | None = None,
        cached_write_price_per_million: Decimal | None = None,
        context_window: int | None = None,
        max_output_tokens: int | None = None,
        capabilities: list[str] | None = None,
        billing_tier: BillingTier = BillingTier.PAY_AS_YOU_GO,
        endpoint_types: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> FakeModel:
        row = FakeModel(
            scope=FakeModelScope.SYSTEM,
            owner_user_id=None,
            model_id=model_id,
            display_name=display_name,
            owned_by=owned_by,
            description=description,
            created_by_user_id=created_by_user_id,
            input_price_per_million=input_price_per_million,
            output_price_per_million=output_price_per_million,
            cached_input_price_per_million=cached_input_price_per_million,
            cached_write_price_per_million=cached_write_price_per_million,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            capabilities=capabilities or [],
            billing_tier=billing_tier,
            endpoint_types=(
                list(endpoint_types)
                if endpoint_types
                else [endpoint.value for endpoint in ModelEndpointType]
            ),
            tags=tags or [],
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
            for model_id, meta in spec["models"].items():
                model = self.find_system_by_model_id(session, model_id)
                if model is None:
                    model = self.create_system_model(
                        session,
                        model_id=model_id,
                        display_name=meta.get("display_name", model_id),
                        owned_by=spec["owned_by"],
                        created_by_user_id=admin_id,
                        description=meta.get("description"),
                        input_price_per_million=(
                            Decimal(str(meta["input"])) if meta.get("input") is not None else None
                        ),
                        output_price_per_million=(
                            Decimal(str(meta["output"])) if meta.get("output") is not None else None
                        ),
                        cached_input_price_per_million=(
                            Decimal(str(meta["cached"])) if meta.get("cached") is not None else None
                        ),
                        cached_write_price_per_million=(
                            Decimal(str(meta["cached_write"]))
                            if meta.get("cached_write") is not None
                            else None
                        ),
                        context_window=meta.get("context"),
                        max_output_tokens=meta.get("max_output"),
                        capabilities=meta.get("capabilities", []),
                        billing_tier=BillingTier(meta.get("billing", "pay_as_you_go")),
                        endpoint_types=meta.get("endpoints"),
                        tags=meta.get("tags", []),
                    )
                    session.flush()
                model_pks.append(model.id)
            if not self.group_items(session, group.id):
                self.replace_group_items(session, group.id, model_pks)
