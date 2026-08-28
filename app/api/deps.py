"""鉴权依赖：会话 token 解析当前用户。"""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..domain.enums import UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.models import User
from ..services.auth_service import AuthService

bearer = HTTPBearer(auto_error=False)


def require_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise DomainError(DomainErrorCode.UNAUTHORIZED, "登录已失效", status_code=401)
    user = AuthService().get_user_by_token(db, credentials.credentials)
    if user is None:
        raise DomainError(DomainErrorCode.UNAUTHORIZED, "登录已失效", status_code=401)
    return user


def require_admin(user: User = Depends(require_current_user)) -> User:
    if user.role is not UserRole.ADMIN:
        raise DomainError(DomainErrorCode.FORBIDDEN, "需要管理员权限", status_code=403)
    return user
