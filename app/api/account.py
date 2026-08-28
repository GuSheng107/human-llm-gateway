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


class PasswordChange(StrictModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=512)


@router.patch("/profile", response_model=CurrentUser)
def update_profile(
    payload: ProfileUpdate,
    user: User = Depends(require_full_session),
    db: Session = Depends(get_db),
) -> CurrentUser:
    UserService().update_display_name(db, user, payload.display_name, actor_user_id=user.id)
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
