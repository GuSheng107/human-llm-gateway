from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import UserRole
from ..models import AdminUser, AuditLog
from ..schemas import CurrentUserSummary, UserCreate
from ..security import hash_password
from .deps import require_admin
from .errors import ApiError, ErrorCode

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[CurrentUserSummary])
def list_users(
    admin: AdminUser = Depends(require_admin), db: Session = Depends(get_db)
) -> list[CurrentUserSummary]:
    users = db.execute(select(AdminUser).order_by(AdminUser.id)).scalars()
    return [
        CurrentUserSummary(
            id=u.id, username=u.username, display_name=u.display_name or u.username, role=u.role
        )
        for u in users
    ]


@router.post("", response_model=CurrentUserSummary)
def create_user(
    payload: UserCreate, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)
) -> CurrentUserSummary:
    if payload.role is UserRole.ADMIN:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "本接口只创建普通用户")
    if db.execute(
        select(AdminUser).where(AdminUser.username == payload.username)
    ).scalar_one_or_none():
        raise ApiError(ErrorCode.CONFLICT, "用户名已存在", status_code=409)
    user = AdminUser(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        role=UserRole.USER,
    )
    db.add(user)
    db.flush()
    db.add(
        AuditLog(
            action="user.created",
            subject_type="user",
            subject_id=str(user.id),
            actor=admin.username,
            detail_json=json.dumps({"username": user.username}, ensure_ascii=False),
        )
    )
    db.commit()
    return CurrentUserSummary(
        id=user.id, username=user.username, display_name=user.display_name, role=user.role
    )
