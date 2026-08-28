from __future__ import annotations

from fastapi import Depends, Header, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..connectors import ConnectorManager
from ..db import get_db
from ..enums import UserRole
from ..models import AdminUser, ApiKey, IMConnection, LLMProvider, ModelRoute
from ..security import verify_admin_token
from ..services import find_api_key
from .errors import ApiError, ErrorAction, ErrorCode

bearer = HTTPBearer(auto_error=False)


def require_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> AdminUser:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise ApiError(
            ErrorCode.AUTH_EXPIRED,
            "登录已失效",
            status_code=status.HTTP_401_UNAUTHORIZED,
            action=ErrorAction.RELOGIN,
        )
    username = verify_admin_token(credentials.credentials, settings.app_secret)
    if not username:
        raise ApiError(
            ErrorCode.AUTH_EXPIRED,
            "登录已失效",
            status_code=status.HTTP_401_UNAUTHORIZED,
            action=ErrorAction.RELOGIN,
        )
    user = db.execute(
        select(AdminUser).where(AdminUser.username == username, AdminUser.active.is_(True))
    ).scalar_one_or_none()
    if user is None:
        raise ApiError(
            ErrorCode.AUTH_EXPIRED,
            "登录已失效",
            status_code=status.HTTP_401_UNAUTHORIZED,
            action=ErrorAction.RELOGIN,
        )
    return user


def require_admin(user: AdminUser = Depends(require_current_user)) -> AdminUser:
    if user.role is not UserRole.ADMIN:
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "需要管理员权限",
            status_code=status.HTTP_403_FORBIDDEN,
            action=ErrorAction.NONE,
        )
    return user


def require_api_key(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> ApiKey:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(
            ErrorCode.AUTH_EXPIRED,
            "缺少 API Key",
            status_code=status.HTTP_401_UNAUTHORIZED,
            action=ErrorAction.NONE,
        )
    key = find_api_key(db, authorization[7:].strip())
    if not key:
        raise ApiError(
            ErrorCode.AUTH_EXPIRED,
            "API Key 无效",
            status_code=status.HTTP_401_UNAUTHORIZED,
            action=ErrorAction.NONE,
        )
    return key


def get_connector_manager(request: Request) -> ConnectorManager:
    manager: ConnectorManager | None = getattr(request.app.state, "connector_manager", None)
    if manager is None:
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            "连接管理器未就绪",
            status_code=500,
            action=ErrorAction.NONE,
        )
    return manager


def get_managed_connection(db: Session, user: AdminUser, connection_id: int) -> IMConnection:
    """归属校验：非管理员只能操作自己的连接。"""
    connection = db.execute(
        select(IMConnection).where(
            IMConnection.id == connection_id, IMConnection.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if connection is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "连接不存在",
            status_code=404,
            action=ErrorAction.NONE,
        )
    if user.role is not UserRole.ADMIN and connection.owner_id != user.id:
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "无权管理其他用户的连接",
            status_code=403,
            action=ErrorAction.NONE,
        )
    return connection


def get_owned_provider(db: Session, user: AdminUser, provider_id: int) -> LLMProvider:
    provider = db.get(LLMProvider, provider_id)
    if provider is None or not provider.active:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "供应商不存在",
            status_code=404,
            action=ErrorAction.NONE,
        )
    if user.role is not UserRole.ADMIN and provider.owner_id != user.id:
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "无权管理其他用户的供应商",
            status_code=403,
            action=ErrorAction.NONE,
        )
    return provider


def get_owned_route(db: Session, user: AdminUser, route_id: int) -> ModelRoute:
    route = db.get(ModelRoute, route_id)
    if route is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "路由不存在",
            status_code=404,
            action=ErrorAction.NONE,
        )
    if user.role is not UserRole.ADMIN and route.owner_id != user.id:
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "无权管理其他用户的路由",
            status_code=403,
            action=ErrorAction.NONE,
        )
    return route


def pagination_params(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict[str, int]:
    return {"page": page, "page_size": page_size}


def paginate(items: list, total: int, params: dict[str, int]) -> dict:
    return {
        "items": items,
        "total": total,
        "page": params["page"],
        "page_size": params["page_size"],
    }
