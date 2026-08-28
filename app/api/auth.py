"""认证 API：登录、登出、当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..domain.enums import UserRole
from ..repositories.models import User
from ..services.auth_service import AuthService
from .deps import require_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str
    password: str


class CurrentUser(BaseModel):
    id: int
    username: str
    display_name: str
    role: UserRole


class LoginResponse(CurrentUser):
    access_token: str
    token_type: str = Field(default="bearer")


def _to_summary(user: User) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
    )


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
