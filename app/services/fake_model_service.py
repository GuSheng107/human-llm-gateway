"""Fake Model 与模型分组用例。

管理员维护全局系统模型；普通用户维护仅自己可见的私有模型。
模型分组作为第一层候选集筛选，不能引用其他用户的私有模型。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.db import begin_immediate_if_sqlite
from ..domain.enums import AuditAction, FakeModelScope, UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.catalog import FakeModelRepository
from ..repositories.models import FakeModel, ModelGroup, User
from ..repositories.system import AuditRepository

# 对外 model 字符串：兼容 OpenAI 常见命名习惯。
MODEL_ID_PATTERN = re.compile(r"^[\w.\-/]{1,255}$")


def _validate_model_id(model_id: str) -> str:
    normalized = (model_id or "").strip()
    if not MODEL_ID_PATTERN.match(normalized):
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "model_id 仅允许字母数字与 . _ - /，长度 1-255",
            status_code=400,
        )
    return normalized


class FakeModelService:
    def __init__(self) -> None:
        self.catalog = FakeModelRepository()
        self.audit = AuditRepository()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_for_user(
        self, session: Session, user: User, *, search: str | None = None
    ) -> list[FakeModel]:
        """普通用户：系统模型 + 自己的私有模型（私有遮蔽同名系统模型）。"""
        rows = self.catalog.visible_models(session, user.id, only_enabled=False)
        if search:
            term = search.strip().lower()
            rows = [
                row
                for row in rows
                if term in row.model_id.lower()
                or (row.display_name and term in row.display_name.lower())
            ]
        return rows

    def list_governance(
        self, session: Session, *, page: int, page_size: int, search: str | None = None
    ) -> tuple[list[FakeModel], int]:
        """管理员治理列表：全部模型。"""
        return self.catalog.list_governance(session, page=page, page_size=page_size, search=search)

    def get_visible(self, session: Session, user: User, model_pk: int) -> FakeModel:
        row = self.catalog.get(session, model_pk)
        if row is None:
            raise DomainError(DomainErrorCode.NOT_FOUND, "模型不存在", status_code=404)
        if user.role is UserRole.ADMIN:
            return row
        if row.scope is FakeModelScope.PRIVATE and row.owner_user_id != user.id:
            # 防越权：其他用户的私有模型按不存在处理。
            raise DomainError(DomainErrorCode.NOT_FOUND, "模型不存在", status_code=404)
        return row

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def create(
        self,
        session: Session,
        *,
        actor: User,
        model_id: str,
        display_name: str | None = None,
        description: str | None = None,
        sort_order: int = 0,
        is_enabled: bool = True,
    ) -> FakeModel:
        begin_immediate_if_sqlite(session)
        normalized = _validate_model_id(model_id)
        scope = FakeModelScope.SYSTEM if actor.role is UserRole.ADMIN else FakeModelScope.PRIVATE
        owner_user_id = None if scope is FakeModelScope.SYSTEM else actor.id
        if scope is FakeModelScope.SYSTEM:
            if self.catalog.find_system_by_model_id(session, normalized) is not None:
                raise DomainError(
                    DomainErrorCode.CONFLICT, "系统模型已存在同名 model_id", status_code=409
                )
        else:
            if self.catalog.find_private_by_model_id(session, actor.id, normalized) is not None:
                raise DomainError(DomainErrorCode.CONFLICT, "你已存在同名私有模型", status_code=409)
        row = FakeModel(
            scope=scope,
            owner_user_id=owner_user_id,
            model_id=normalized,
            display_name=(display_name or "").strip() or None,
            owned_by="human-llm-gateway" if scope is FakeModelScope.SYSTEM else "user",
            description=description,
            sort_order=sort_order,
            is_enabled=is_enabled,
            created_by_user_id=actor.id,
        )
        self.catalog.add(session, row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DomainError(DomainErrorCode.CONFLICT, "模型标识冲突", status_code=409) from exc
        self.audit.add(
            session,
            action=AuditAction.FAKE_MODEL_CREATED,
            resource_type="fake_model",
            resource_id=str(row.id),
            actor_user_id=actor.id,
            owner_user_id=owner_user_id,
            metadata={"fields": ["model_id", "scope"]},
        )
        return row

    def update(
        self,
        session: Session,
        *,
        row: FakeModel,
        actor: User,
        fields: dict[str, Any],
    ) -> FakeModel:
        self._ensure_manageable(row, actor)
        allowed = {"display_name", "description", "sort_order", "is_enabled"}
        changed: list[str] = []
        for name in allowed:
            if name not in fields:
                continue
            value = fields[name]
            if name == "display_name":
                value = (value or "").strip() or None
            if getattr(row, name) != value:
                setattr(row, name, value)
                changed.append(name)
        if changed:
            session.flush()
            self.audit.add(
                session,
                action=AuditAction.FAKE_MODEL_UPDATED,
                resource_type="fake_model",
                resource_id=str(row.id),
                actor_user_id=actor.id,
                owner_user_id=row.owner_user_id,
                metadata={"fields": changed},
            )
        return row

    def delete(self, session: Session, *, row: FakeModel, actor: User) -> None:
        self._ensure_manageable(row, actor)
        self.catalog.soft_delete(session, row.id)
        self.audit.add(
            session,
            action=AuditAction.FAKE_MODEL_DELETED,
            resource_type="fake_model",
            resource_id=str(row.id),
            actor_user_id=actor.id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["deleted_at"]},
        )

    def _ensure_manageable(self, row: FakeModel, actor: User) -> None:
        """普通用户只能管理自己的私有模型；管理员可治理全部但不能转授。"""
        if actor.role is UserRole.ADMIN:
            return
        if row.scope is FakeModelScope.PRIVATE and row.owner_user_id == actor.id:
            return
        raise DomainError(DomainErrorCode.FORBIDDEN, "无权管理该模型", status_code=403)


class ModelGroupService:
    def __init__(self) -> None:
        self.catalog = FakeModelRepository()
        self.models = FakeModelService()
        self.audit = AuditRepository()

    def get_owned(self, session: Session, group_pk: int, user: User) -> ModelGroup:
        row = self.catalog.get_group(session, group_pk)
        if row is None or (user.role is not UserRole.ADMIN and row.owner_user_id != user.id):
            raise DomainError(DomainErrorCode.NOT_FOUND, "模型分组不存在", status_code=404)
        return row

    def list_for_user(
        self, session: Session, user: User, *, page: int, page_size: int
    ) -> tuple[list[ModelGroup], int]:
        return self.catalog.list_groups(
            session,
            owner_user_id=None if user.role is UserRole.ADMIN else user.id,
            page=page,
            page_size=page_size,
        )

    def create(
        self,
        session: Session,
        *,
        owner: User,
        name: str,
        description: str | None = None,
        is_enabled: bool = True,
    ) -> ModelGroup:
        begin_immediate_if_sqlite(session)
        name = (name or "").strip()
        if not name or len(name) > 100:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "分组名称不能为空且最多 100 字符",
                status_code=400,
            )
        row = ModelGroup(
            owner_user_id=owner.id,
            name=name,
            description=(description or "").strip() or None,
            is_enabled=is_enabled,
        )
        self.catalog.add_group(session, row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DomainError(DomainErrorCode.CONFLICT, "同名分组已存在", status_code=409) from exc
        self.audit.add(
            session,
            action=AuditAction.MODEL_GROUP_CREATED,
            resource_type="model_group",
            resource_id=str(row.id),
            actor_user_id=owner.id,
            owner_user_id=owner.id,
            metadata={"fields": ["name"]},
        )
        return row

    def update(
        self, session: Session, *, row: ModelGroup, actor: User, fields: dict[str, Any]
    ) -> ModelGroup:
        changed: list[str] = []
        if "name" in fields:
            new_name = (fields["name"] or "").strip()
            if not new_name or len(new_name) > 100:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "分组名称不能为空且最多 100 字符",
                    status_code=400,
                )
            if new_name != row.name:
                row.name = new_name
                changed.append("name")
        if "description" in fields:
            row.description = (fields["description"] or "").strip() or None
            changed.append("description")
        if (
            "is_enabled" in fields
            and fields["is_enabled"] is not None
            and bool(fields["is_enabled"]) != row.is_enabled
        ):
            row.is_enabled = bool(fields["is_enabled"])
            changed.append("is_enabled")
        try:
            session.flush()
        except IntegrityError as exc:
            raise DomainError(DomainErrorCode.CONFLICT, "同名分组已存在", status_code=409) from exc
        if changed:
            self.audit.add(
                session,
                action=AuditAction.MODEL_GROUP_UPDATED,
                resource_type="model_group",
                resource_id=str(row.id),
                actor_user_id=actor.id,
                owner_user_id=row.owner_user_id,
                metadata={"fields": changed},
            )
        return row

    def replace_members(
        self, session: Session, *, row: ModelGroup, actor: User, fake_model_ids: list[int]
    ) -> ModelGroup:
        """原子替换分组成员；成员必须属于组所有者的可见模型集合。"""
        visible_ids = {
            model.id for model in self.catalog.visible_models(session, row.owner_user_id)
        }
        invalid = [mid for mid in fake_model_ids if mid not in visible_ids]
        if invalid:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "分组只能包含当前用户可见的模型",
                status_code=400,
            )
        self.catalog.replace_group_items(session, row.id, fake_model_ids)
        self.audit.add(
            session,
            action=AuditAction.MODEL_GROUP_UPDATED,
            resource_type="model_group",
            resource_id=str(row.id),
            actor_user_id=actor.id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["members"]},
        )
        return row

    def delete(self, session: Session, *, row: ModelGroup, actor: User) -> None:
        references = self.catalog.count_enabled_api_key_references_for_group(session, row.id)
        if references:
            raise DomainError(
                DomainErrorCode.CONFLICT, "分组仍被启用的 API Key 引用", status_code=409
            )
        self.catalog.soft_delete_group(session, row.id)
        self.audit.add(
            session,
            action=AuditAction.MODEL_GROUP_DELETED,
            resource_type="model_group",
            resource_id=str(row.id),
            actor_user_id=actor.id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["deleted_at"]},
        )
