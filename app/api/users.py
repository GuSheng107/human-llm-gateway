"""管理员用户治理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.security import generate_temporary_password
from ..core.time import iso_utc
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.models import User
from ..repositories.users import UserRepository
from ..services.user_service import UserService
from .common import StrictModel
from .deps import require_admin

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCreate(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    password: str | None = Field(default=None, max_length=512)


class UserUpdate(StrictModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None

    @field_validator("display_name")
    @classmethod
    def reject_null_display_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("显示名不能为空")
        return value

    @field_validator("is_active")
    @classmethod
    def reject_null_status(cls, value: bool | None) -> bool:
        if value is None:
            raise ValueError("用户状态不能为空")
        return value


class PasswordReset(StrictModel):
    password: str | None = Field(default=None, max_length=512)


class UserSummary(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
    active_task_count: int
    registered_via_invitation_id: str | None
    last_login_at: str | None
    disabled_at: str | None
    created_at: str


class UserImpact(BaseModel):
    active_sessions: int
    enabled_api_keys: int
    active_tasks: int


class UserDetail(UserSummary):
    impact: UserImpact
    resource_counts: dict[str, int]


class UserCreated(UserSummary):
    temporary_password: str | None


class PasswordResetResult(BaseModel):
    user: UserSummary
    temporary_password: str | None


class UserPage(BaseModel):
    items: list[UserSummary]
    page: int
    page_size: int
    total: int


def _summary(user: User) -> UserSummary:
    return UserSummary(
        id=str(user.id),
        username=user.username,
        display_name=user.display_name,
        role=user.role.value,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        active_task_count=user.active_task_count,
        registered_via_invitation_id=(
            str(user.registered_via_invitation_id)
            if user.registered_via_invitation_id is not None
            else None
        ),
        last_login_at=iso_utc(user.last_login_at),
        disabled_at=iso_utc(user.disabled_at),
        created_at=iso_utc(user.created_at) or "",
    )


def _detail(db: Session, user: User) -> UserDetail:
    service = UserService()
    return UserDetail(
        **_summary(user).model_dump(),
        impact=UserImpact(**service.impact_counts(db, user.id)),
        resource_counts=service.users.resource_counts(db, user.id),
    )


def _get_user(db: Session, user_id: int) -> User:
    user = UserRepository().get_by_id(db, user_id)
    if user is None:
        raise DomainError(DomainErrorCode.NOT_FOUND, "用户不存在", status_code=404)
    return user


@router.get("", response_model=UserPage)
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    is_active: bool | None = Query(default=None),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserPage:
    rows, total = UserRepository().list_page(
        db, page=page, page_size=page_size, search=search, is_active=is_active
    )
    return UserPage(
        items=[_summary(user) for user in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=UserCreated, status_code=201)
def create_user(
    payload: UserCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserCreated:
    generated = payload.password is None
    password = payload.password or generate_temporary_password()
    try:
        user = UserService().create_user(
            db,
            username=payload.username,
            display_name=payload.display_name,
            password=password,
            must_change_password=True,
            actor_user_id=admin.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DomainError(DomainErrorCode.CONFLICT, "用户名已存在", status_code=409) from exc
    return UserCreated(
        **_summary(user).model_dump(), temporary_password=password if generated else None
    )


@router.get("/{user_id}", response_model=UserDetail)
def get_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserDetail:
    return _detail(db, _get_user(db, user_id))


@router.patch("/{user_id}", response_model=UserDetail)
def update_user(
    user_id: int,
    payload: UserUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserDetail:
    target = _get_user(db, user_id)
    service = UserService()
    fields = payload.model_fields_set
    if "display_name" in fields:
        service.update_display_name(db, target, payload.display_name, actor_user_id=admin.id)
    if "is_active" in fields:
        if payload.is_active:
            service.enable_user(db, target, actor_user_id=admin.id)
        else:
            service.disable_user(db, target, actor_user_id=admin.id)
    db.commit()
    db.refresh(target)
    return _detail(db, target)


@router.post("/{user_id}/reset-password", response_model=PasswordResetResult)
def reset_password(
    user_id: int,
    payload: PasswordReset,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PasswordResetResult:
    target = _get_user(db, user_id)
    generated = payload.password is None
    password = payload.password or generate_temporary_password()
    UserService().reset_password(db, target, password, actor_user_id=admin.id)
    db.commit()
    return PasswordResetResult(
        user=_summary(target), temporary_password=password if generated else None
    )
