from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..config import get_settings
from ..connectors import connector_registry
from ..db import get_db
from ..enums import UserRole
from ..im_connections import (
    binding_status_snapshot,
    connection_summary,
    create_user_connection,
    list_connections,
    mark_connection_applied,
    soft_delete_connection,
    start_binding,
    update_connection_config,
)
from ..models import AdminUser, AuditLog
from ..schemas import (
    BindingStartResponse,
    ConnectionCreate,
    ConnectionCreated,
    ConnectionSummary,
)
from .deps import get_connector_manager, get_managed_connection, require_current_user
from .errors import ApiError, ErrorAction, ErrorCode

router = APIRouter(prefix="/api", tags=["connections"])


@router.get("/im-platforms")
def list_im_platforms(user: AdminUser = Depends(require_current_user)) -> list[dict[str, Any]]:
    return [definition.public_dict() for definition in connector_registry.all()]


@router.get("/im-connections", response_model=list[ConnectionSummary])
def list_connections_endpoint(
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> list[ConnectionSummary]:
    return list_connections(db, user)


@router.post("/im-connections", response_model=ConnectionCreated)
async def create_connection(
    payload: ConnectionCreate,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    manager=Depends(get_connector_manager),
) -> ConnectionCreated:
    connection, created = create_user_connection(
        db,
        user,
        name=payload.name,
        platform=payload.platform,
        raw_config=payload.config,
        registry=connector_registry,
    )
    await manager.configure(connection)
    db.refresh(connection)
    return ConnectionCreated(**connection_summary(connection).model_dump(), setup=created.setup)


@router.get("/im-connections/{connector_id}", response_model=ConnectionSummary)
def get_connection(
    connector_id: int,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ConnectionSummary:
    connection = get_managed_connection(db, user, connector_id)
    return connection_summary(connection)


@router.post("/im-connections/{connector_id}/update", response_model=ConnectionSummary)
def update_connection(
    connector_id: int,
    payload: dict[str, Any] = Body(default={}),
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> ConnectionSummary:
    if user.role is UserRole.ADMIN:
        raise ApiError(ErrorCode.FORBIDDEN, "管理员不能编辑用户连接配置", status_code=403)
    connection = get_managed_connection(db, user, connector_id)
    name = payload.get("name")
    raw_config = payload.get("config") or {}
    if not isinstance(raw_config, dict):
        raise ApiError(ErrorCode.VALIDATION_FAILED, "config 必须是对象")
    connection = update_connection_config(
        db,
        user,
        connection,
        name=name,
        raw_config=raw_config,
        registry=connector_registry,
    )
    return connection_summary(connection)


@router.post("/im-connections/{connector_id}/delete")
async def delete_connection(
    connector_id: int,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    manager=Depends(get_connector_manager),
) -> dict[str, Any]:
    connection = get_managed_connection(db, user, connector_id)
    await manager.stop_connection(connector_id)
    soft_delete_connection(db, user, connection)
    return {"deleted": True}


@router.post("/im-connections/{connector_id}/start")
async def start_connector(
    connector_id: int,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    manager=Depends(get_connector_manager),
) -> dict[str, Any]:
    connection = get_managed_connection(db, user, connector_id)
    await manager.configure(connection)
    return await manager.health(connector_id)


@router.post("/im-connections/{connector_id}/stop")
async def stop_connector(
    connector_id: int,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    manager=Depends(get_connector_manager),
) -> dict[str, Any]:
    connection = get_managed_connection(db, user, connector_id)
    is_admin_op = user.role is UserRole.ADMIN and connection.owner_id != user.id
    await manager.stop_connection(connector_id)
    if is_admin_op:
        db.add(
            AuditLog(
                action="connector.force_stop",
                subject_type="im_connection",
                subject_id=str(connector_id),
                actor=user.username,
                detail_json="{}",
            )
        )
        db.commit()
    return {"stopped": True}


@router.post("/im-connections/{connector_id}/apply")
async def apply_connection(
    connector_id: int,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    manager=Depends(get_connector_manager),
) -> ConnectionSummary:
    if user.role is UserRole.ADMIN:
        raise ApiError(ErrorCode.FORBIDDEN, "管理员不能应用用户连接配置", status_code=403)
    connection = get_managed_connection(db, user, connector_id)
    await manager.configure(connection)
    mark_connection_applied(db, user, connection)
    return connection_summary(connection)


@router.get("/im-connections/{connector_id}/health")
async def connector_health(
    connector_id: int,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    manager=Depends(get_connector_manager),
) -> dict[str, Any]:
    get_managed_connection(db, user, connector_id)
    return await manager.health(connector_id)


@router.post("/im-connections/{connector_id}/login")
async def connector_login(
    connector_id: int,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    manager=Depends(get_connector_manager),
) -> dict[str, Any]:
    connection = get_managed_connection(db, user, connector_id)
    if user.role is UserRole.ADMIN:
        raise ApiError(ErrorCode.FORBIDDEN, "管理员不能发起用户 Bot 登录", status_code=403)
    if manager.get(connector_id) is None:
        await manager.configure(connection)
    try:
        return await manager.login(connector_id)
    except RuntimeError as exc:
        raise ApiError(
            ErrorCode.CONNECTOR_ERROR, str(exc), status_code=400, action=ErrorAction.RETRY_START
        ) from exc


@router.get("/im-connections/{connector_id}/login")
async def connector_login_state(
    connector_id: int,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    manager=Depends(get_connector_manager),
) -> dict[str, Any]:
    connection = get_managed_connection(db, user, connector_id)
    if user.role is UserRole.ADMIN:
        raise ApiError(ErrorCode.FORBIDDEN, "管理员不能读取用户 Bot 登录凭据", status_code=403)
    connector = manager.get(connector_id)
    if connector is None:
        await manager.configure(connection)
        connector = manager.get(connector_id)
    if connector is None or not hasattr(connector, "login_snapshot"):
        raise ApiError(ErrorCode.VALIDATION_FAILED, "该平台无扫码登录流程", status_code=400)
    return connector.login_snapshot()


@router.post("/im-connections/{connector_id}/binding", response_model=BindingStartResponse)
def begin_binding(
    connector_id: int,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> BindingStartResponse:
    from fastapi import HTTPException

    connection = get_managed_connection(db, user, connector_id)
    try:
        return start_binding(db, user, connection, get_settings())
    except HTTPException as exc:
        if exc.status_code == 423:
            raise ApiError(
                ErrorCode.BINDING_LOCKED,
                str(exc.detail),
                status_code=423,
                action=ErrorAction.WAIT_AND_RETRY,
            ) from exc
        raise
    except Exception as exc:
        raise ApiError(ErrorCode.CONNECTOR_ERROR, str(exc)) from exc


@router.get("/im-connections/{connector_id}/binding/status")
def binding_status(
    connector_id: int,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    connection = get_managed_connection(db, user, connector_id)
    return binding_status_snapshot(db, connection)
