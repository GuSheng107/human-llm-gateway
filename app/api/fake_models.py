"""Fake Model 与模型分组 API（docs/API_CONTRACT.md §7）。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc
from ..domain.enums import FakeModelScope, UserRole
from ..repositories.models import FakeModel, ModelGroup, User
from ..services.fake_model_service import FakeModelService, ModelGroupService
from .common import StrictModel
from .deps import require_current_user

router = APIRouter(prefix="/api/fake-models", tags=["fake-models"])
groups_router = APIRouter(prefix="/api/model-groups", tags=["model-groups"])

_models = FakeModelService()
_groups = ModelGroupService()


class ModelPricing(StrictModel):
    input: Decimal | None = None
    output: Decimal | None = None
    cached_input: Decimal | None = None
    cached_write: Decimal | None = None


class FakeModelCreate(StrictModel):
    model_id: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    sort_order: int = 0
    enabled: bool = True
    pricing: ModelPricing | None = None
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    capabilities: list[str] = Field(default_factory=list, max_length=16)
    billing_tier: str | None = None
    endpoint_types: list[str] | None = Field(default=None, max_length=8)
    logo_url: str | None = Field(default=None, max_length=512)
    tags: list[str] = Field(default_factory=list, max_length=20)
    group_ids: list[int] = Field(default_factory=list, max_length=100)


class FakeModelUpdate(StrictModel):
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    sort_order: int | None = None
    enabled: bool | None = None
    input_price_per_million: Decimal | None = None
    output_price_per_million: Decimal | None = None
    cached_input_price_per_million: Decimal | None = None
    cached_write_price_per_million: Decimal | None = None
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    capabilities: list[str] | None = Field(default=None, max_length=16)
    billing_tier: str | None = None
    endpoint_types: list[str] | None = Field(default=None, max_length=8)
    logo_url: str | None = Field(default=None, max_length=512)
    tags: list[str] | None = Field(default=None, max_length=20)
    group_ids: list[int] | None = Field(default=None, max_length=100)


class FakeModelView(BaseModel):
    id: str
    scope: str
    owner_user_id: str | None
    model_id: str
    display_name: str | None
    owned_by: str
    description: str | None
    sort_order: int
    is_enabled: bool
    input_price_per_million: float | None
    output_price_per_million: float | None
    cached_input_price_per_million: float | None
    cached_write_price_per_million: float | None
    context_window: int | None
    max_output_tokens: int | None
    capabilities: list[str]
    billing_tier: str
    endpoint_types: list[str]
    logo_url: str | None
    tags: list[str]
    created_at: str


class FakeModelPage(BaseModel):
    items: list[FakeModelView]
    page: int
    page_size: int
    total: int


class GroupCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool = True


class GroupUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class GroupMembersUpdate(StrictModel):
    fake_model_ids: list[int]


class GroupView(BaseModel):
    id: str
    owner_user_id: str
    name: str
    description: str | None
    is_enabled: bool
    model_ids: list[str]
    is_public: bool
    can_manage: bool
    can_assign_model: bool
    created_at: str


class GroupPage(BaseModel):
    items: list[GroupView]
    page: int
    page_size: int
    total: int


def _model_view(row: FakeModel) -> FakeModelView:
    return FakeModelView(
        id=str(row.id),
        scope=row.scope.value,
        owner_user_id=str(row.owner_user_id) if row.owner_user_id else None,
        model_id=row.model_id,
        display_name=row.display_name,
        owned_by=row.owned_by,
        description=row.description,
        sort_order=row.sort_order,
        is_enabled=row.is_enabled,
        input_price_per_million=(
            float(row.input_price_per_million) if row.input_price_per_million is not None else None
        ),
        output_price_per_million=(
            float(row.output_price_per_million)
            if row.output_price_per_million is not None
            else None
        ),
        cached_input_price_per_million=(
            float(row.cached_input_price_per_million)
            if row.cached_input_price_per_million is not None
            else None
        ),
        cached_write_price_per_million=(
            float(row.cached_write_price_per_million)
            if row.cached_write_price_per_million is not None
            else None
        ),
        context_window=row.context_window,
        max_output_tokens=row.max_output_tokens,
        capabilities=list(row.capabilities or []),
        billing_tier=row.billing_tier.value,
        endpoint_types=list(row.endpoint_types or []),
        logo_url=row.logo_url,
        tags=list(row.tags or []),
        created_at=iso_utc(row.created_at) or "",
    )


def _group_view(session: Session, row: ModelGroup, user: User) -> GroupView:
    items = _models.catalog.group_items(session, row.id)
    if user.role is not UserRole.ADMIN:
        items = [
            item
            for item in items
            if item.scope is FakeModelScope.SYSTEM or item.owner_user_id == user.id
        ]
    return GroupView(
        id=str(row.id),
        owner_user_id=str(row.owner_user_id),
        name=row.name,
        description=row.description,
        is_enabled=row.is_enabled,
        model_ids=[item.model_id for item in items],
        is_public=row.is_public,
        can_manage=user.role is UserRole.ADMIN or row.owner_user_id == user.id,
        can_assign_model=(
            user.role is UserRole.ADMIN or row.is_public or row.owner_user_id == user.id
        ),
        created_at=iso_utc(row.created_at) or "",
    )


# ---------------------------------------------------------------------------
# Fake Model
# ---------------------------------------------------------------------------


@router.get("", response_model=FakeModelPage)
def list_fake_models(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    search: str | None = Query(default=None, max_length=255),
    provider: str | None = Query(default=None, max_length=100),
    billing_tier: str | None = Query(default=None, max_length=32),
    endpoint_type: str | None = Query(default=None, max_length=32),
    capability: str | None = Query(default=None, max_length=32),
    tag: str | None = Query(default=None, max_length=64),
    group_id: int | None = Query(default=None, ge=1),
    include_disabled: bool = Query(default=False),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Any:
    filters = {
        key: value
        for key, value in {
            "provider": provider,
            "billing_tier": billing_tier,
            "endpoint_type": endpoint_type,
            "capability": capability,
            "tag": tag,
        }.items()
        if value
    }
    filters["enabled_only"] = not include_disabled
    if group_id is not None:
        group = _groups.get_visible(db, group_id, user)
        filters["model_ids"] = {model.id for model in _models.catalog.group_items(db, group.id)}
    if user.role is UserRole.ADMIN:
        rows, total = _models.list_governance(
            db, page=page, page_size=page_size, search=search, filters=filters
        )
    else:
        rows = _models.list_for_user(db, user, search=search, filters=filters)
        total = len(rows)
        rows = rows[(page - 1) * page_size : page * page_size]
    return FakeModelPage(
        items=[_model_view(row) for row in rows], page=page, page_size=page_size, total=total
    )


@router.post("", response_model=FakeModelView, status_code=201)
def create_fake_model(
    payload: FakeModelCreate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> FakeModelView:
    row = _models.create(
        db,
        actor=user,
        model_id=payload.model_id,
        display_name=payload.display_name,
        description=payload.description,
        sort_order=payload.sort_order,
        is_enabled=payload.enabled,
        pricing=(
            {
                "input": payload.pricing.input,
                "output": payload.pricing.output,
                "cached_input": payload.pricing.cached_input,
                "cached_write": payload.pricing.cached_write,
            }
            if payload.pricing
            else None
        ),
        context_window=payload.context_window,
        max_output_tokens=payload.max_output_tokens,
        capabilities=payload.capabilities,
        billing_tier=payload.billing_tier,
        endpoint_types=payload.endpoint_types,
        logo_url=payload.logo_url,
        tags=payload.tags,
        group_ids=payload.group_ids,
    )
    db.commit()
    db.refresh(row)
    return _model_view(row)


@router.get("/{model_id}", response_model=FakeModelView)
def get_fake_model(
    model_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> FakeModelView:
    return _model_view(_models.get_visible(db, user, model_id))


@router.patch("/{model_id}", response_model=FakeModelView)
def update_fake_model(
    model_id: int,
    payload: FakeModelUpdate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> FakeModelView:
    row = _models.get_visible(db, user, model_id)
    fields = payload.model_dump(include=payload.model_fields_set)
    if "enabled" in fields:
        fields["is_enabled"] = fields.pop("enabled")
    updated = _models.update(db, row=row, actor=user, fields=fields)
    db.commit()
    db.refresh(updated)
    return _model_view(updated)


@router.delete("/{model_id}", status_code=204)
def delete_fake_model(
    model_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = _models.get_visible(db, user, model_id)
    _models.delete(db, row=row, actor=user)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 模型分组
# ---------------------------------------------------------------------------


@groups_router.get("", response_model=GroupPage)
def list_model_groups(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> GroupPage:
    rows, total = _groups.list_for_user(db, user, page=page, page_size=page_size)
    return GroupPage(
        items=[_group_view(db, row, user) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@groups_router.post("", response_model=GroupView, status_code=201)
def create_model_group(
    payload: GroupCreate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> GroupView:
    row = _groups.create(
        db,
        owner=user,
        name=payload.name,
        description=payload.description,
        is_enabled=payload.enabled,
    )
    db.commit()
    db.refresh(row)
    return _group_view(db, row, user)


def _get_group(db: Session, group_id: int, user: User) -> ModelGroup:
    return _groups.get_owned(db, group_id, user)


@groups_router.get("/{group_id}", response_model=GroupView)
def get_model_group(
    group_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> GroupView:
    return _group_view(db, _groups.get_visible(db, group_id, user), user)


@groups_router.patch("/{group_id}", response_model=GroupView)
def update_model_group(
    group_id: int,
    payload: GroupUpdate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> GroupView:
    row = _get_group(db, group_id, user)
    fields = payload.model_dump(include=payload.model_fields_set)
    if "enabled" in fields:
        fields["is_enabled"] = fields.pop("enabled")
    updated = _groups.update(db, row=row, actor=user, fields=fields)
    db.commit()
    db.refresh(updated)
    return _group_view(db, updated, user)


@groups_router.put("/{group_id}/models", response_model=GroupView)
def replace_group_members(
    group_id: int,
    payload: GroupMembersUpdate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> GroupView:
    row = _get_group(db, group_id, user)
    updated = _groups.replace_members(
        db, row=row, actor=user, fake_model_ids=payload.fake_model_ids
    )
    db.commit()
    db.refresh(updated)
    return _group_view(db, updated, user)


@groups_router.delete("/{group_id}", status_code=204)
def delete_model_group(
    group_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = _get_group(db, group_id, user)
    _groups.delete(db, row=row, actor=user)
    db.commit()
    return Response(status_code=204)


# 供 API Key 服务/测试使用的作用域常量
__all__ = ["FakeModelScope", "groups_router", "router"]
