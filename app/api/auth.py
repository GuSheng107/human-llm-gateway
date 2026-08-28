"""认证 API：登录、登出、当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..domain.enums import Capability, UserRole
from ..repositories.models import User
from ..services.auth_service import AuthService
from .common import StrictModel
from .deps import require_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class RegisterRequest(StrictModel):
    invitation_code: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=512)


class CurrentUser(BaseModel):
    id: str
    username: str
    display_name: str
    role: UserRole
    must_change_password: bool
    capabilities: list[Capability]


class LoginResponse(CurrentUser):
    access_token: str
    token_type: str = Field(default="bearer")


def _to_summary(user: User) -> CurrentUser:
    capabilities = [Capability.ACCOUNT_PASSWORD_CHANGE]
    if not user.must_change_password:
        capabilities.append(Capability.ACCOUNT_PROFILE_UPDATE)
        if user.role is UserRole.ADMIN:
            capabilities.extend([Capability.INVITATION_MANAGE, Capability.USER_MANAGE])
    return CurrentUser(
        id=str(user.id),
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
        must_change_password=user.must_change_password,
        capabilities=capabilities,
    )


@router.post("/register", response_model=CurrentUser, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> CurrentUser:
    user = AuthService().register(
        db,
        invitation_code=payload.invitation_code,
        username=payload.username,
        display_name=payload.display_name,
        password=payload.password,
    )
    return _to_summary(user)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    token, _expires_at, user = AuthService().login(db, payload.username, payload.password)
    return LoginResponse(**_to_summary(user).model_dump(), access_token=token)


@router.post("/logout", status_code=204)
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Response:
    if credentials is not None:
        AuthService().logout(db, credentials.credentials)
    return Response(status_code=204)


@router.get("/me", response_model=CurrentUser)
def current_user(user: User = Depends(require_current_user)) -> CurrentUser:
    return _to_summary(user)
