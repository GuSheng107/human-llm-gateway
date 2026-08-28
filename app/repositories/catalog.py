"""Fake Model 目录仓库：系统模型、私有模型与默认种子。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.time import utc_now
from ..domain.enums import FakeModelScope
from .models import FakeModel

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
    def get_by_id(self, session: Session, model_id_pk: int) -> FakeModel | None:
        return session.get(FakeModel, model_id_pk)

    def find_system_by_model_id(self, session: Session, model_id: str) -> FakeModel | None:
        return session.execute(
            select(FakeModel).where(
                FakeModel.scope == FakeModelScope.SYSTEM,
                FakeModel.model_id == model_id,
                FakeModel.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

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
