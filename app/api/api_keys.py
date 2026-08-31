"""API Key 管理 API（docs/API_CONTRACT.md §8）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc
from ..domain.enums import DeliveryMode, ReplyStrategy, UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.catalog import FakeModelRepository
from ..repositories.models import ApiKey, User
from ..services.api_key_service import ApiKeyService
from .common import StrictModel
from .deps import require_current_user

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])

_service = ApiKeyService()
_catalog = FakeModelRepository()


class ApiKeyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    delivery_mode: DeliveryMode = DeliveryMode.WEB
    im_connection_id: int | None = None
    reply_strategy: ReplyStrategy = ReplyStrategy.HUMAN
    llm_config_id: int | None = None
    human_timeout_seconds: int = Field(default=300, ge=10, le=1800)
    model_group_id: int | None = None
    fake_model_ids: list[int] = Field(default_factory=list)


class ApiKeyUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    delivery_mode: DeliveryMode | None = None
    im_connection_id: int | None = None
    reply_strategy: ReplyStrategy | None = None
    llm_config_id: int | None = None
    human_timeout_seconds: int | None = Field(default=None, ge=10, le=1800)
    model_group_id: int | None = None
    fake_model_ids: list[int] | None = None


class ApiKeyView(BaseModel):
    id: str
    name: str
    key_prefix: str
    is_enabled: bool
    delivery_mode: str
    im_connection_id: str | None
    reply_strategy: str
    llm_config_id: str | None
    human_timeout_seconds: int
    model_group_id: str | None
    fake_model_ids: list[str]
    fake_model_names: list[str]
    last_used_at: str | None
    created_at: str
    owner_user_id: str | None = None
    owner_username: str | None = None


class ApiKeyCreated(ApiKeyView):
    plaintext: str


class ApiKeyPage(BaseModel):
    items: list[ApiKeyView]
    page: int
    page_size: int
    total: int


def _view(session: Session, row: ApiKey, *, include_owner: bool = False) -> ApiKeyView:
    owner_username = None
    if include_owner:
        owner = session.get(User, row.owner_user_id)
        owner_username = owner.username if owner else None
    selected = _catalog.key_selected_models(session, row.id)
    return ApiKeyView(
        id=str(row.id),
        name=row.name,
        key_prefix=row.key_prefix,
        is_enabled=row.is_enabled,
        delivery_mode=row.delivery_mode.value,
        im_connection_id=str(row.im_connection_id) if row.im_connection_id else None,
        reply_strategy=row.reply_strategy.value,
        llm_config_id=str(row.llm_config_id) if row.llm_config_id else None,
        human_timeout_seconds=row.human_timeout_seconds,
        model_group_id=str(row.model_group_id) if row.model_group_id else None,
        fake_model_ids=[str(model.id) for model in selected],
        fake_model_names=[model.model_id for model in selected],
        last_used_at=iso_utc(row.last_used_at),
        created_at=iso_utc(row.created_at) or "",
        owner_user_id=str(row.owner_user_id) if include_owner else None,
        owner_username=owner_username,
    )


@router.get("", response_model=ApiKeyPage)
def list_api_keys(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyPage:
    owner_filter = None if user.role is UserRole.ADMIN else user.id
    rows, total = _service.repo.list_page(
        db, page=page, page_size=page_size, owner_user_id=owner_filter, search=search
    )
    return ApiKeyPage(
        items=[_view(db, row, include_owner=user.role is UserRole.ADMIN) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=ApiKeyCreated, status_code=201)
def create_api_key(
    payload: ApiKeyCreate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyCreated:
    _assert_admin_can_manage(user)
    row, plaintext = _service.create(
        db,
        owner=user,
        name=payload.name,
        delivery_mode=payload.delivery_mode,
        im_connection_id=payload.im_connection_id,
        reply_strategy=payload.reply_strategy,
        llm_config_id=payload.llm_config_id,
        human_timeout_seconds=payload.human_timeout_seconds,
        model_group_id=payload.model_group_id,
        fake_model_ids=payload.fake_model_ids,
    )
    db.commit()
    db.refresh(row)
    return ApiKeyCreated(**_view(db, row).model_dump(), plaintext=plaintext)


def _assert_admin_can_manage(user: User) -> None:
    """管理员只监管 API Key（停用/删除），不能以管理员身份新建或改写 Key。"""
    if user.role is UserRole.ADMIN:
        raise DomainError(DomainErrorCode.FORBIDDEN, "管理员不能创建 API Key", status_code=403)


def _get_key(db: Session, key_id: int, user: User) -> ApiKey:
    return _service.get_owned(db, key_id, user)


@router.get("/{key_id}", response_model=ApiKeyView)
def get_api_key(
    key_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyView:
    row = _get_key(db, key_id, user)
    return _view(db, row, include_owner=user.role is UserRole.ADMIN)


@router.patch("/{key_id}", response_model=ApiKeyView)
def update_api_key(
    key_id: int,
    payload: ApiKeyUpdate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyView:
    row = _get_key(db, key_id, user)
    fields = payload.model_dump(include=payload.model_fields_set)
    if user.role is UserRole.ADMIN:
        # 管理员仅允许启停治理，不能改写 Key 的其他配置。
        disallowed = set(fields) - {"enabled"}
        if disallowed:
            raise DomainError(
                DomainErrorCode.FORBIDDEN,
                "管理员仅能停用或启用 API Key",
                status_code=403,
            )
    _service.update(db, row=row, actor=user, fields=fields)
    db.commit()
    db.refresh(row)
    return _view(db, row)


@router.delete("/{key_id}", status_code=204)
def delete_api_key(
    key_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = _get_key(db, key_id, user)
    _service.delete(db, row=row, actor=user)
    db.commit()
    return Response(status_code=204)
