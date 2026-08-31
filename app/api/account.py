"""当前用户账号 API：显示名与自助改密。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.security import hash_session_token
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.models import User
from ..services.user_service import UserService
from .auth import CurrentUser, _to_summary
from .common import StrictModel
from .deps import bearer, require_current_user, require_full_session

router = APIRouter(prefix="/api/account", tags=["account"])


class ProfileUpdate(StrictModel):
    display_name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    avatar_base64: str | None = Field(default=None, max_length=2_000_000)


class PasswordChange(StrictModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=512)


@router.patch("/profile", response_model=CurrentUser)
def update_profile(
    payload: ProfileUpdate,
    user: User = Depends(require_full_session),
    db: Session = Depends(get_db),
) -> CurrentUser:
    fields = payload.model_fields_set
    UserService().update_profile(
        db,
        user,
        display_name=payload.display_name,
        # 显式 null 代表清空；字段缺失才代表保持不变。
        email=("" if payload.email is None else payload.email) if "email" in fields else None,
        avatar_base64=("" if payload.avatar_base64 is None else payload.avatar_base64)
        if "avatar_base64" in fields
        else None,
        actor_user_id=user.id,
    )
    db.commit()
    return _to_summary(user)


class PasswordForcedChange(StrictModel):
    new_password: str = Field(min_length=1, max_length=512)


@router.post("/password", response_model=CurrentUser)
def change_password(
    payload: PasswordChange,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> CurrentUser:
    UserService().change_password(
        db,
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    db.commit()
    return _to_summary(user)


# 强制改密（must_change_password=true 的受限会话）：不校验旧密码。
@router.post("/password/forced", response_model=CurrentUser)
def change_password_forced(
    payload: PasswordForcedChange,
    request_credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if not user.must_change_password:
        raise DomainError(DomainErrorCode.FORBIDDEN, "仅受限会话可强制改密", status_code=403)
    if request_credentials is None:
        raise DomainError(DomainErrorCode.UNAUTHORIZED, "登录已失效", status_code=401)
    UserService().set_password(
        db,
        user,
        new_password=payload.new_password,
        keep_token_hash=hash_session_token(request_credentials.credentials),
    )
    db.commit()
    return _to_summary(user)
