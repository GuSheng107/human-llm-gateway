"""IM 连接管理 API（docs/API_CONTRACT.md §5）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..core.db import get_db
from ..core.time import iso_utc
from ..domain.enums import ConnectionState, UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.models import ImConnection, User
from ..services.connection_service import ConnectionService
from .common import StrictModel
from .deps import require_current_user

router = APIRouter(prefix="/api/im-connections", tags=["im-connections"])
platforms_router = APIRouter(prefix="/api/im-platforms", tags=["im-platforms"])

_service = ConnectionService()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ConnectionCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    platform: str = Field(min_length=1, max_length=50)
    config: dict[str, Any]


class ConnectionUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    config: dict[str, Any] | None = None


class ConnectionView(BaseModel):
    id: str
    name: str
    platform: str
    platform_label: str
    state: str
    desired_running: bool
    bound: bool
    owner_user_id: str | None = None
    owner_username: str | None = None
    config: dict[str, Any]
    last_error_code: str | None
    last_error_message: str | None
    retry_count: int
    next_retry_at: str | None
    last_health_at: str | None
    created_at: str


class ConnectionPage(BaseModel):
    items: list[ConnectionView]
    page: int
    page_size: int
    total: int


class ConnectionHealth(BaseModel):
    state: str
    desired_running: bool
    retry_count: int
    next_retry_at: str | None
    last_authenticated_at: str | None
    last_health_at: str | None
    last_error_code: str | None
    last_error_message: str | None
    runtime: dict[str, Any]


class ConnectionCheckItem(ConnectionHealth):
    id: str
    name: str
    platform: str
    platform_label: str
    owner_username: str | None = None
    bound: bool
    abnormal: bool
    auto_disabled: bool


class BindingCreated(BaseModel):
    binding_code: str
    expires_at: str


class BindingStatus(BaseModel):
    bound: bool
    binding_pending: bool
    binding_expires_at: str | None


class PlatformField(BaseModel):
    name: str
    label: str
    type: str
    required: bool
    secret: bool
    description: str


class PlatformView(BaseModel):
    code: str
    label: str
    description: str
    kind: str
    supports_delivery: bool
    supports_login: bool
    config_schema: list[PlatformField]


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def _view(session: Session, row: ImConnection, *, include_owner: bool = False) -> ConnectionView:
    owner_username = None
    if include_owner:
        from ..repositories.models import User

        owner = session.get(User, row.owner_user_id)
        owner_username = owner.username if owner else None
    return ConnectionView(
        id=str(row.id),
        name=row.name,
        platform=row.platform,
        platform_label=_service.registry.get_spec(row.platform).label
        if _service.registry.get_spec(row.platform)
        else row.platform,
        state=row.state.value,
        desired_running=row.desired_running,
        bound=row.bound_external_user_id is not None,
        owner_user_id=str(row.owner_user_id) if include_owner else None,
        owner_username=owner_username,
        config=_service._public_config(row),
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        retry_count=row.retry_count,
        next_retry_at=iso_utc(row.next_retry_at),
        last_health_at=iso_utc(row.last_health_at),
        created_at=iso_utc(row.created_at) or "",
    )


# ---------------------------------------------------------------------------
# 平台目录
# ---------------------------------------------------------------------------


@platforms_router.get("", response_model=list[PlatformView])
def list_platforms(_user: User = Depends(require_current_user)) -> list[PlatformView]:
    return [
        PlatformView(
            code=spec.code,
            label=spec.label,
            description=spec.description,
            kind=spec.kind,
            supports_delivery=spec.supports_delivery,
            supports_login=spec.supports_login,
            config_schema=[PlatformField(**field) for field in spec.config_schema()],
        )
        for spec in _service.registry.list_specs()
    ]


# ---------------------------------------------------------------------------
# 连接 CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=ConnectionPage)
def list_connections(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    platform: str | None = Query(default=None, max_length=50),
    state: ConnectionState | None = Query(default=None),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ConnectionPage:
    owner_filter = None if user.role is UserRole.ADMIN else user.id
    rows, total = _service.repo.list_page(
        db,
        page=page,
        page_size=page_size,
        owner_user_id=owner_filter,
        search=search,
        platform=platform,
        state=state,
    )
    return ConnectionPage(
        items=[_view(db, row, include_owner=user.role is UserRole.ADMIN) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=ConnectionView, status_code=201)
def create_connection(
    payload: ConnectionCreate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ConnectionView:
    row = _service.create(
        db, owner=user, name=payload.name, platform=payload.platform, config=payload.config
    )
    db.commit()
    db.refresh(row)
    return _view(db, row)


@router.post("/check", response_model=list[ConnectionCheckItem])
async def check_connections(
    user: User = Depends(require_current_user),
) -> list[ConnectionCheckItem]:
    """立即执行与后台看门狗相同的检查，并停用当前异常连接。"""
    from ..services.connection_watchdog import connection_watchdog

    owner_filter = None if user.role is UserRole.ADMIN else user.id
    reports = await connection_watchdog.check_once(owner_user_id=owner_filter)
    return [ConnectionCheckItem(**report) for report in reports]


def _get_visible_connection(db: Session, connection_id: int, user: User) -> ImConnection:
    if user.role is UserRole.ADMIN:
        return _service.get(db, connection_id)
    return _service.get_owned(db, connection_id, user.id)


@router.get("/{connection_id}", response_model=ConnectionView)
def get_connection(
    connection_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ConnectionView:
    row = _get_visible_connection(db, connection_id, user)
    return _view(db, row, include_owner=user.role is UserRole.ADMIN)


@router.patch("/{connection_id}", response_model=ConnectionView)
def update_connection(
    connection_id: int,
    payload: ConnectionUpdate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ConnectionView:
    row = _get_visible_connection(db, connection_id, user)
    if user.role is UserRole.ADMIN and payload.config is not None:
        # 管理员只能治理（名称/启停/删除），不能读取或修改用户凭据。
        raise DomainError(DomainErrorCode.FORBIDDEN, "管理员不能修改连接配置", status_code=403)
    fields = payload.model_dump(include=payload.model_fields_set, exclude_none=False)
    _service.update(
        db,
        row=row,
        actor_user_id=user.id,
        name=fields.get("name"),
        config_changes=payload.config,
    )
    db.commit()
    db.refresh(row)
    return _view(db, row)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = await run_in_threadpool(_get_visible_connection, db, connection_id, user)
    await _service.delete(db, row=row, actor_user_id=user.id)
    await run_in_threadpool(db.commit)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


@router.post("/{connection_id}/start", response_model=ConnectionView)
async def start_connection(
    connection_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ConnectionView:
    row = await run_in_threadpool(_get_visible_connection, db, connection_id, user)
    await _service.start(db, row=row, actor_user_id=user.id)
    await run_in_threadpool(db.commit)
    await run_in_threadpool(db.refresh, row)
    return await run_in_threadpool(_view, db, row)


@router.post("/{connection_id}/stop", response_model=ConnectionView)
async def stop_connection(
    connection_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ConnectionView:
    row = await run_in_threadpool(_get_visible_connection, db, connection_id, user)
    await _service.stop(db, row=row, actor_user_id=user.id)
    await run_in_threadpool(db.commit)
    await run_in_threadpool(db.refresh, row)
    return await run_in_threadpool(_view, db, row)


@router.post("/{connection_id}/apply", response_model=ConnectionView)
async def apply_connection(
    connection_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ConnectionView:
    row = await run_in_threadpool(_get_visible_connection, db, connection_id, user)
    await _service.apply(db, row=row, actor_user_id=user.id)
    await run_in_threadpool(db.commit)
    await run_in_threadpool(db.refresh, row)
    return await run_in_threadpool(_view, db, row)


@router.get("/{connection_id}/health", response_model=ConnectionHealth)
def connection_health(
    connection_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ConnectionHealth:
    row = _get_visible_connection(db, connection_id, user)
    return ConnectionHealth(**_service.health(db, row))


# ---------------------------------------------------------------------------
# 登录与绑定（仅所有者）
# ---------------------------------------------------------------------------


def _require_owner(row: ImConnection, user: User) -> None:
    if user.id != row.owner_user_id:
        raise DomainError(DomainErrorCode.FORBIDDEN, "仅连接所有者可以执行该操作", status_code=403)


@router.post("/{connection_id}/login")
async def start_login(
    connection_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = _service.get_owned(db, connection_id, user.id)
    _require_owner(row, user)
    result = await _service.start_login(db, row=row, actor_user_id=user.id)
    await run_in_threadpool(db.commit)
    return result


@router.get("/{connection_id}/login")
async def poll_login(
    connection_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = _service.get_owned(db, connection_id, user.id)
    _require_owner(row, user)
    result = await _service.poll_login(db, row=row, actor_user_id=user.id)
    await run_in_threadpool(db.commit)
    return result


@router.post("/{connection_id}/binding", response_model=BindingCreated)
def create_binding(
    connection_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> BindingCreated:
    row = _service.get_owned(db, connection_id, user.id)
    _require_owner(row, user)
    result = _service.create_binding_code(db, row=row, actor_user_id=user.id)
    db.commit()
    return BindingCreated(**result)


@router.get("/{connection_id}/binding/status", response_model=BindingStatus)
def binding_status(
    connection_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> BindingStatus:
    row = _service.get_owned(db, connection_id, user.id)
    return BindingStatus(**_service.binding_status(db, row))
