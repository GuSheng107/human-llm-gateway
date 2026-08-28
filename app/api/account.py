"""当前用户账号 API：显示名与自助改密。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..repositories.models import User
from ..services.user_service import UserService
from .auth import CurrentUser, _to_summary
from .common import StrictModel
from .deps import require_current_user, require_full_session

router = APIRouter(prefix="/api/account", tags=["account"])


class ProfileUpdate(StrictModel):
    display_name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    avatar_base64: str | None = Field(default=None, max_length=400_000)


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
