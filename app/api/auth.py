"""认证 API：登录、登出、当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core import login_throttle
from ..core.captcha import allow_captcha_request, generate_captcha, verify_captcha
from ..core.db import get_db
from ..domain.enums import Capability, UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.models import User
from ..services.auth_service import AuthService
from .common import StrictModel
from .deps import require_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _throttle_key(request: Request, username: str) -> str:
    """节流 key：client_ip|username 组合。

    反代场景下 request.client.host 是代理 IP——纯 IP 计数会把全部用户
    聚合误锁（10 次失败 DoS 全站）。组合 key 下同一 IP 对不同用户名
    各自计数，对同一用户名的爆破仍被窗口上限拦截。
    """
    normalized = (username or "").strip().lower()[:128]
    return f"{_client_ip(request)}|{normalized}"


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    captcha_token: str = Field(min_length=1, max_length=64)
    captcha_code: str = Field(min_length=1, max_length=8)


class CaptchaResponse(BaseModel):
    captcha_token: str
    captcha_image: str


class RegisterRequest(StrictModel):
    invitation_code: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=512)
    email: str | None = Field(default=None, max_length=255)
    captcha_token: str = Field(min_length=1, max_length=64)
    captcha_code: str = Field(min_length=1, max_length=8)


class CurrentUser(BaseModel):
    id: str
    username: str
    display_name: str
    role: UserRole
    must_change_password: bool
    capabilities: list[Capability]
    email: str | None = None
    avatar_base64: str | None = None


class LoginResponse(CurrentUser):
    access_token: str
    token_type: str = Field(default="bearer")


def _to_summary(user: User) -> CurrentUser:
    capabilities = [Capability.ACCOUNT_PASSWORD_CHANGE]
    if not user.must_change_password:
        capabilities.append(Capability.ACCOUNT_PROFILE_UPDATE)
        capabilities.extend(
            [
                Capability.CONNECTION_MANAGE,
                Capability.MODEL_MANAGE,
                Capability.API_KEY_MANAGE,
            ]
        )
        if user.role is UserRole.ADMIN:
            capabilities.extend([Capability.INVITATION_MANAGE, Capability.USER_MANAGE])
    return CurrentUser(
        id=str(user.id),
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
        must_change_password=user.must_change_password,
        capabilities=capabilities,
        email=user.email,
        avatar_base64=user.avatar_base64,
    )


@router.post("/register", response_model=CurrentUser, status_code=201)
def register(
    payload: RegisterRequest, request: Request, db: Session = Depends(get_db)
) -> CurrentUser:
    # 注册失败节流（与登录同窗口）：验证码/邀请码错误均计数，防脚本枚举。
    key = _throttle_key(request, payload.username)
    if not login_throttle.allow(key):
        raise DomainError(
            DomainErrorCode.RATE_LIMIT_EXCEEDED,
            "尝试过于频繁，请稍后再试",
            status_code=429,
        )
    if not verify_captcha(payload.captcha_token, payload.captcha_code):
        login_throttle.record_failure(key)
        raise DomainError(DomainErrorCode.VALIDATION_FAILED, "验证码错误或已过期", status_code=400)
    try:
        user = AuthService().register(
            db,
            invitation_code=payload.invitation_code,
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
            email=payload.email,
        )
    except DomainError as exc:
        if exc.code is DomainErrorCode.INVALID_INVITATION:
            login_throttle.record_failure(key)
        raise
    login_throttle.reset(key)
    return _to_summary(user)


@router.get("/captcha", response_model=CaptchaResponse)
def captcha(request: Request) -> CaptchaResponse:
    source = _client_ip(request)
    if not allow_captcha_request(source):
        raise DomainError(
            DomainErrorCode.RATE_LIMIT_EXCEEDED,
            "验证码请求过于频繁，请稍后再试",
            status_code=429,
        )
    token, image = generate_captcha()
    return CaptchaResponse(captcha_token=token, captcha_image=image)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    key = _throttle_key(request, payload.username)
    if not login_throttle.allow(key):
        raise DomainError(
            DomainErrorCode.RATE_LIMIT_EXCEEDED,
            "登录尝试过于频繁，请稍后再试",
            status_code=429,
        )
    if not verify_captcha(payload.captcha_token, payload.captcha_code):
        login_throttle.record_failure(key)
        raise DomainError(DomainErrorCode.VALIDATION_FAILED, "验证码错误或已过期", status_code=400)
    try:
        token, _expires_at, user = AuthService().login(db, payload.username, payload.password)
    except DomainError as exc:
        if exc.code is DomainErrorCode.UNAUTHORIZED:
            login_throttle.record_failure(key)
        raise
    login_throttle.reset(key)
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
