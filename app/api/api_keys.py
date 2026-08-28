from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..db import get_db
from ..enums import BindingStatus, UserRole
from ..models import AdminUser, ApiKey, AuditLog, HumanOperator, IMConnection, ModelRoute
from ..schemas import ApiKeyCreate, ApiKeyCreated
from ..security import generate_api_key
from .deps import paginate, pagination_params, require_current_user
from .errors import ApiError, ErrorAction, ErrorCode

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


def _key_summary(k: ApiKey) -> dict[str, Any]:
    connection = k.im_connection
    return {
        "id": k.id,
        "name": k.name,
        "prefix": k.prefix,
        "active": k.active,
        "operator_name": k.human_operator.display_name,
        "im_name": connection.name if connection else "",
        "im_connection_id": k.im_connection_id,
        "binding_type": "im" if k.im_connection_id is not None else "web",
        "platform": connection.platform if connection else None,
        "route_id": k.route_id,
        "route_mode": k.route.mode,
        "model_name": k.route.model_name,
        "owner_id": k.owner_id,
        "created_at": k.created_at.isoformat(),
    }


@router.get("")
def list_keys(
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    params: dict = Depends(pagination_params),
) -> dict[str, Any]:
    stmt = select(ApiKey).options(
        joinedload(ApiKey.human_operator),
        joinedload(ApiKey.im_connection),
        joinedload(ApiKey.route),
    )
    if user.role is not UserRole.ADMIN:
        stmt = stmt.where(ApiKey.owner_id == user.id)
    stmt = stmt.order_by(ApiKey.id.desc())
    all_keys = list(db.execute(stmt).scalars().unique())
    total = len(all_keys)
    start = (params["page"] - 1) * params["page_size"]
    return paginate(
        [_key_summary(k) for k in all_keys[start : start + params["page_size"]]], total, params
    )


@router.post("", response_model=ApiKeyCreated)
def create_key(
    payload: ApiKeyCreate,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_current_user),
) -> ApiKeyCreated:
    # 路由必须是自己的（管理员也不能用别人的路由建 Key）
    route = db.get(ModelRoute, payload.route_id) if payload.route_id else None
    if route is None:
        raise ApiError(ErrorCode.NOT_FOUND, "路由不存在")
    if route.owner_id != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "只能绑定自己的路由", status_code=403)

    connection: IMConnection | None = None
    if payload.im_connection_id is not None:
        connection = db.get(IMConnection, payload.im_connection_id)
        if connection is None or connection.deleted_at is not None:
            raise ApiError(ErrorCode.NOT_FOUND, "IM 连接不存在")
        if user.role is not UserRole.ADMIN and connection.owner_id != user.id:
            raise ApiError(ErrorCode.FORBIDDEN, "只能绑定自己的连接")
        if connection.binding_status is not BindingStatus.BOUND:
            raise ApiError(
                ErrorCode.VALIDATION_FAILED,
                "该连接尚未完成身份绑定，请先在 IM 中完成绑定",
                status_code=422,
                action=ErrorAction.REGENERATE_BINDING,
            )
    secret, prefix, secret_hash = generate_api_key()
    operator = HumanOperator(display_name=payload.operator_name or user.username, status="offline")
    db.add(operator)
    db.flush()
    key = ApiKey(
        name=payload.name,
        prefix=prefix,
        secret_hash=secret_hash,
        human_operator_id=operator.id,
        im_connection_id=connection.id if connection else None,
        route_id=route.id,
        owner_id=user.id,
    )
    db.add(key)
    db.add(
        AuditLog(
            action="api_key.created",
            subject_type="api_key",
            subject_id="new",
            actor=user.username,
            detail_json=json.dumps(
                {"prefix": prefix, "binding_type": "im" if connection else "web"},
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    summary = _key_summary(key)
    return ApiKeyCreated(**summary, secret=secret)


@router.post("/{key_id}/delete")
def delete_key(
    key_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_current_user)
) -> dict[str, Any]:
    key = db.get(ApiKey, key_id)
    if key is None:
        raise ApiError(ErrorCode.NOT_FOUND, "API Key 不存在")
    if user.role is not UserRole.ADMIN and key.owner_id != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "只能删除自己的 API Key")
    db.add(
        AuditLog(
            action="api_key.deleted",
            subject_type="api_key",
            subject_id=str(key.id),
            actor=user.username,
            detail_json=json.dumps({"prefix": key.prefix}),
        )
    )
    db.delete(key)
    db.commit()
    return {"deleted": True}


@router.post("/{key_id}/disable")
def disable_key(
    key_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_current_user)
) -> dict[str, Any]:
    key = db.get(ApiKey, key_id)
    if key is None:
        raise ApiError(ErrorCode.NOT_FOUND, "API Key 不存在")
    if user.role is not UserRole.ADMIN and key.owner_id != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "只能操作自己的 API Key")
    key.active = not key.active
    db.add(
        AuditLog(
            action="api_key.toggled",
            subject_type="api_key",
            subject_id=str(key.id),
            actor=user.username,
            detail_json=json.dumps({"active": key.active}),
        )
    )
    db.commit()
    return {"active": key.active}
