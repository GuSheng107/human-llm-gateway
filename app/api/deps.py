"""鉴权依赖：会话 token 解析当前用户，并全局兜底受限会话。"""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..domain.enums import UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.models import User
from ..services.auth_service import AuthService

bearer = HTTPBearer(auto_error=False)

# DATABASE §3.1：受限会话（must_change_password=true）仅允许这三类端点。
_RESTRICTED_SESSION_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/auth/me"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/account/password"),
        ("POST", "/api/account/password/forced"),
    }
)


def require_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
    request: Request = None,  # FastAPI 注入
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise DomainError(DomainErrorCode.UNAUTHORIZED, "登录已失效", status_code=401)
    user = AuthService().get_user_by_token(db, credentials.credentials)
    if user is None:
        raise DomainError(DomainErrorCode.UNAUTHORIZED, "登录已失效", status_code=401)
    # 绑定日志用户上下文：同线程内的 log_event / 审计调用自动携带。
    # 同时写入 scope.state，请求中间件统一访问日志（不同线程池 Context）
    # 依赖该显式取值。
    from ..core.logging import bind_log_user

    bind_log_user(user.id, user.role.value)
    request.scope.setdefault("state", {})["log_user"] = (
        user.id,
        user.role.value,
        user.username,
    )
    # 全局兜底：受限会话除白名单外一律 403，防止后续新增端点漏加 require_full_session。
    if (
        user.must_change_password
        and (request.method, request.url.path) not in _RESTRICTED_SESSION_ALLOWLIST
    ):
        raise DomainError(DomainErrorCode.FORBIDDEN, "请先修改临时密码", status_code=403)
    return user


def require_full_session(user: User = Depends(require_current_user)) -> User:
    return user


def require_admin(user: User = Depends(require_current_user)) -> User:
    if user.role is not UserRole.ADMIN:
        raise DomainError(DomainErrorCode.FORBIDDEN, "需要管理员权限", status_code=403)
    return user
