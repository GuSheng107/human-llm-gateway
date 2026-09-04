"""Fake Model 与模型分组用例。

管理员维护全局系统模型；普通用户维护仅自己可见的私有模型。
模型分组作为第一层候选集筛选，不能引用其他用户的私有模型。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.db import begin_immediate_if_sqlite
from ..domain.enums import (
    AuditAction,
    BillingTier,
    FakeModelScope,
    ModelEndpointType,
    UserRole,
)
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.catalog import FakeModelRepository
from ..repositories.models import FakeModel, ModelGroup, User
from ..repositories.system import AuditRepository

# 对外 model 字符串：兼容 OpenAI 常见命名习惯。
MODEL_ID_PATTERN = re.compile(r"^[\w.\-/]{1,255}$")

# 模型能力标签白名单（超出部分忽略）。
# 函数调用与工具调用业内已统一为 tool calling，这里只保留 tools 一项。
ALLOWED_CAPABILITIES = {
    "vision",
    "tools",
    "thinking",
    "image_gen",
    "audio",
    "video",
    "streaming",
}


def _clean_capabilities(values: list[str] | None) -> list[str]:
    """去重并只保留白名单内的能力标签（历史 function_calling 归并为 tools）。"""
    cleaned = _clean_tags(values)
    if "function_calling" in cleaned:
        cleaned = [item for item in cleaned if item != "function_calling"]
        if "tools" not in cleaned:
            cleaned.append("tools")
    return [item for item in cleaned if item in ALLOWED_CAPABILITIES]


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED, "价格必须是数字", status_code=400
        ) from exc
    if result < 0:
        raise DomainError(DomainErrorCode.VALIDATION_FAILED, "价格不能为负数", status_code=400)
    return result


def _clean_endpoint_types(values: list[str] | None) -> list[str]:
    """端点多选：None 表示全开三种协议；显式给定必须非空且值合法。"""
    if values is None:
        return [endpoint.value for endpoint in ModelEndpointType]
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        try:
            ModelEndpointType(normalized)
        except ValueError as exc:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"未知端点协议 {normalized}",
                status_code=400,
            ) from exc
        seen.add(normalized)
        cleaned.append(normalized)
    if not cleaned:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "至少选择一个对外端点协议",
            status_code=400,
        )
    return cleaned


def _clean_tags(values: list[str] | None) -> list[str]:
    if not values:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result[:20]


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
        self,
        session: Session,
        user: User,
        *,
        search: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[FakeModel]:
        """普通用户：系统模型 + 自己的私有模型（私有遮蔽同名系统模型）。"""
        rows = self.catalog.visible_models(session, user.id, only_enabled=False)
        return self._filter_rows(rows, search=search, filters=filters)

    def list_governance(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[list[FakeModel], int]:
        """管理员治理列表：全部模型（元数据维度过滤在内存完成后再分页）。"""
        rows = self.catalog.list_all_governance(session)
        filtered = self._filter_rows(rows, search=search, filters=filters)
        total = len(filtered)
        return filtered[(page - 1) * page_size : page * page_size], total

    @staticmethod
    def _filter_rows(
        rows: list[FakeModel],
        *,
        search: str | None,
        filters: dict[str, Any] | None,
    ) -> list[FakeModel]:
        result = rows
        if search:
            term = search.strip().lower()
            result = [
                row
                for row in result
                if term in row.model_id.lower()
                or (row.display_name and term in row.display_name.lower())
                or (row.description and term in row.description.lower())
                or any(term in tag.lower() for tag in (row.tags or []))
            ]
        if filters:
            provider = filters.get("provider")
            if provider:
                result = [row for row in result if row.owned_by == provider]
            billing = filters.get("billing_tier")
            if billing:
                result = [row for row in result if row.billing_tier.value == billing]
            endpoint = filters.get("endpoint_type")
            if endpoint:
                result = [row for row in result if endpoint in (row.endpoint_types or [])]
            capability = filters.get("capability")
            if capability:
                result = [row for row in result if capability in (row.capabilities or [])]
            tag = filters.get("tag")
            if tag:
                result = [row for row in result if tag in (row.tags or [])]
            if filters.get("enabled_only"):
                result = [row for row in result if row.is_enabled]
            model_ids = filters.get("model_ids")
            if model_ids is not None:
                result = [row for row in result if row.id in model_ids]
        return result

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
        pricing: dict[str, Any] | None = None,
        context_window: int | None = None,
        max_output_tokens: int | None = None,
        capabilities: list[str] | None = None,
        billing_tier: str | None = None,
        endpoint_types: list[str] | None = None,
        logo_url: str | None = None,
        tags: list[str] | None = None,
        group_ids: list[int] | None = None,
    ) -> FakeModel:
        begin_immediate_if_sqlite(session)
        normalized = _validate_model_id(model_id)
        normalized_group_ids = self._validate_assignable_groups(session, actor, group_ids or [])
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
        pricing = pricing or {}
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
            input_price_per_million=_to_decimal(pricing.get("input")),
            output_price_per_million=_to_decimal(pricing.get("output")),
            cached_input_price_per_million=_to_decimal(pricing.get("cached_input")),
            cached_write_price_per_million=_to_decimal(pricing.get("cached_write")),
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            capabilities=_clean_capabilities(capabilities),
            billing_tier=BillingTier(billing_tier or BillingTier.PAY_AS_YOU_GO.value),
            endpoint_types=_clean_endpoint_types(endpoint_types),
            logo_url=(logo_url or "").strip() or None,
            tags=_clean_tags(tags),
        )
        self.catalog.add(session, row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DomainError(DomainErrorCode.CONFLICT, "模型标识冲突", status_code=409) from exc
        self.catalog.replace_model_groups(session, row.id, normalized_group_ids)
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
        groups_provided = "group_ids" in fields
        group_ids = fields.pop("group_ids", None)
        normalized_group_ids = (
            self._validate_assignable_groups(session, actor, group_ids or [])
            if groups_provided
            else None
        )
        allowed = {
            "display_name",
            "description",
            "sort_order",
            "is_enabled",
            "context_window",
            "max_output_tokens",
            "capabilities",
            "billing_tier",
            "endpoint_types",
            "logo_url",
            "tags",
        }
        pricing_names = {
            "input_price_per_million",
            "output_price_per_million",
            "cached_input_price_per_million",
            "cached_write_price_per_million",
        }
        changed: list[str] = []
        for name in allowed:
            if name not in fields:
                continue
            value = fields[name]
            if name == "display_name" or name == "logo_url":
                value = (value or "").strip() or None
            elif name == "capabilities":
                value = _clean_capabilities(value)
            elif name == "tags":
                value = _clean_tags(value)
            elif name == "billing_tier" and value is not None:
                value = BillingTier(value)
            elif name == "endpoint_types":
                value = _clean_endpoint_types(value)
            if getattr(row, name) != value:
                setattr(row, name, value)
                changed.append(name)
        for name in pricing_names:
            if name not in fields:
                continue
            value = _to_decimal(fields[name])
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
        if groups_provided:
            self.catalog.replace_model_groups(session, row.id, normalized_group_ids or [])
            self.audit.add(
                session,
                action=AuditAction.FAKE_MODEL_UPDATED,
                resource_type="fake_model",
                resource_id=str(row.id),
                actor_user_id=actor.id,
                owner_user_id=row.owner_user_id,
                metadata={"fields": ["group_ids"]},
            )
        return row

    def delete(self, session: Session, *, row: FakeModel, actor: User) -> None:
        self._ensure_manageable(row, actor)
        self.catalog.delete(session, row.id)
        self.audit.add(
            session,
            action=AuditAction.FAKE_MODEL_DELETED,
            resource_type="fake_model",
            resource_id=str(row.id),
            actor_user_id=actor.id,
            owner_user_id=row.owner_user_id,
        )

    def _ensure_manageable(self, row: FakeModel, actor: User) -> None:
        """普通用户只能管理自己的私有模型；管理员可治理全部但不能转授。"""
        if actor.role is UserRole.ADMIN:
            return
        if row.scope is FakeModelScope.PRIVATE and row.owner_user_id == actor.id:
            return
        raise DomainError(DomainErrorCode.FORBIDDEN, "无权管理该模型", status_code=403)

    def _validate_assignable_groups(
        self, session: Session, actor: User, group_ids: list[int]
    ) -> list[int]:
        """校验模型侧分组多选，不允许通过写模型加入不可见私有分组。"""
        result = list(dict.fromkeys(group_ids))
        for group_id in result:
            group = self.catalog.get_group(session, group_id)
            if group is None:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED, "模型分组不存在", status_code=400
                )
            if actor.role is not UserRole.ADMIN and not (
                group.is_public or group.owner_user_id == actor.id
            ):
                raise DomainError(
                    DomainErrorCode.FORBIDDEN, "无权将模型加入该分组", status_code=403
                )
        return result


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

    def get_visible(self, session: Session, group_pk: int, user: User) -> ModelGroup:
        row = self.catalog.get_group(session, group_pk)
        if row is None:
            raise DomainError(DomainErrorCode.NOT_FOUND, "模型分组不存在", status_code=404)
        if user.role is UserRole.ADMIN or row.owner_user_id == user.id or row.is_public:
            return row
        raise DomainError(DomainErrorCode.NOT_FOUND, "模型分组不存在", status_code=404)

    def list_for_user(
        self, session: Session, user: User, *, page: int, page_size: int
    ) -> tuple[list[ModelGroup], int]:
        """管理员看全部分组；普通用户看自己的分组 + 公开分组。"""
        return self.catalog.list_groups(
            session,
            owner_user_id=None if user.role is UserRole.ADMIN else user.id,
            include_public=user.role is not UserRole.ADMIN,
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
            is_public=owner.role is UserRole.ADMIN,
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
        begin_immediate_if_sqlite(session)
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
        if actor.role is not UserRole.ADMIN:
            raise DomainError(
                DomainErrorCode.FORBIDDEN,
                "普通用户不能整体覆盖模型分组成员，请在模型编辑中分配分组",
                status_code=403,
            )
        begin_immediate_if_sqlite(session)
        visible_ids = (
            {model.id for model in self.catalog.list_all_governance(session)}
            if actor.role is UserRole.ADMIN
            else {model.id for model in self.catalog.visible_models(session, row.owner_user_id)}
        )
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
        self.catalog.delete_group(session, row.id)
        self.audit.add(
            session,
            action=AuditAction.MODEL_GROUP_DELETED,
            resource_type="model_group",
            resource_id=str(row.id),
            actor_user_id=actor.id,
            owner_user_id=row.owner_user_id,
        )
