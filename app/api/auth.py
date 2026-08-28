from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..models import AdminUser
from ..schemas import CurrentUserSummary, LoginRequest, LoginResponse
from ..security import issue_admin_token, verify_password
from .deps import require_current_user
from .errors import ApiError, ErrorAction, ErrorCode

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> LoginResponse:
    user = db.execute(
        select(AdminUser).where(AdminUser.username == payload.username, AdminUser.active.is_(True))
    ).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise ApiError(
            ErrorCode.AUTH_EXPIRED, "用户名或密码错误", status_code=401, action=ErrorAction.RELOGIN
        )
    return LoginResponse(
        access_token=issue_admin_token(user.username, settings.app_secret),
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
    )


@router.get("/me", response_model=CurrentUserSummary)
def current_user(user: AdminUser = Depends(require_current_user)) -> CurrentUserSummary:
    return CurrentUserSummary(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
    )
