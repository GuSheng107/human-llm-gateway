"""有效模型集合计算与 Fake Model 校验。

/v1/models 与三个推理入口复用同一个查询（docs/DATABASE.md §6.3），
筛选层只收窄、不扩张：可见集合 -> 模型分组 -> Key 显式选择。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..repositories.catalog import FakeModelRepository
from ..repositories.models import ApiKey, FakeModel, User


class EffectiveModelService:
    def __init__(self) -> None:
        self.catalog = FakeModelRepository()

    # ------------------------------------------------------------------

    def visible_models(self, session: Session, user: User) -> list[FakeModel]:
        return self.catalog.visible_models(session, user.id)

    def effective_models(self, session: Session, key: ApiKey) -> list[FakeModel]:
        candidates = self.catalog.visible_models(session, key.owner_user_id)
        if key.model_group_id is not None:
            group = self.catalog.get_group(session, key.model_group_id)
            if group is None:
                # 分组已被删除：候选集收窄为空（Key 需要改绑分组才能继续使用）。
                return []
            member_ids = {row.id for row in self.catalog.group_items(session, group.id)}
            candidates = [row for row in candidates if row.id in member_ids]
        selected = self.catalog.key_selected_models(session, key.id)
        if selected:
            selected_ids = {row.id for row in selected}
            candidates = [row for row in candidates if row.id in selected_ids]
        return candidates

    def resolve(self, session: Session, key: ApiKey, model_id: str) -> FakeModel | None:
        """按 Fake Model 字符串解析有效模型；不在有效集合时返回 None。"""
        if not model_id:
            return None
        for row in self.effective_models(session, key):
            if row.model_id == model_id:
                return row
        return None

    def catalog_entry(self, row: FakeModel) -> dict[str, object]:
        return {
            "id": row.model_id,
            "object": "model",
            "created": int(row.created_at.timestamp()) if row.created_at else 0,
            "owned_by": row.owned_by or (row.scope.value if row.scope else "human-llm-gateway"),
        }
