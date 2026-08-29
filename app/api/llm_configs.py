"""LLM 配置管理 API（docs/API_CONTRACT.md §6、docs/DATABASE.md §4.4）。

Secret 与自定义 Header 整体不回显：列表与详情只返回"是否设置"标记；
`POST /api/llm-configs/{id}/test` 返回 success / reason_code / http_status，
不返回响应正文。删除被引用配置返回 409。
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.constants import (
    LLM_HEADER_NAME_MAX_LENGTH,
    LLM_HEADER_VALUE_MAX_LENGTH,
    LLM_NAME_MAX_LENGTH,
    LLM_TIMEOUT_DEFAULT_SECONDS,
)
from ..core.db import get_db
from ..core.time import iso_utc
from ..domain.enums import LLMProtocol, UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.models import LlmConfig, User
from ..services.llm_config_service import LlmConfigService
from ..services.llm_test_service import run_connectivity_test
from .common import StrictModel
from .deps import require_current_user

router = APIRouter(prefix="/api/llm-configs", tags=["llm-configs"])

_service = LlmConfigService()


# ----------------------------------------------------------------------
# 请求模型
# ----------------------------------------------------------------------


class LlmConfigHeaderSpec(StrictModel):
    name: str = Field(min_length=1, max_length=LLM_HEADER_NAME_MAX_LENGTH)
    value: str = Field(default="", max_length=LLM_HEADER_VALUE_MAX_LENGTH)


class LlmConfigCreate(StrictModel):
    name: str = Field(min_length=1, max_length=LLM_NAME_MAX_LENGTH)
    protocol: LLMProtocol
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1)
    timeout_seconds: int = Field(default=LLM_TIMEOUT_DEFAULT_SECONDS, ge=5, le=600)
    headers: list[LlmConfigHeaderSpec] = Field(default_factory=list)
    enabled: bool = True


class LlmConfigUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=LLM_NAME_MAX_LENGTH)
    protocol: LLMProtocol | None = None
    base_url: str | None = Field(default=None, min_length=1)
    api_key: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    timeout_seconds: int | None = Field(default=None, ge=5, le=600)
    headers: list[LlmConfigHeaderSpec] | None = None
    enabled: bool | None = None


# ----------------------------------------------------------------------
# 视图模型
# ----------------------------------------------------------------------


class LlmConfigHeaderView(BaseModel):
    name: str
    value_set: bool


class LlmConfigView(BaseModel):
    id: str
    name: str
    protocol: str
    base_url: str
    base_url_host: str
    real_model: str
    timeout_seconds: int
    is_enabled: bool
    api_key_set: bool
    headers: list[LlmConfigHeaderView]
    last_tested_at: str | None
    last_test_result: str | None
    created_at: str
    updated_at: str
    owner_user_id: str | None = None
    owner_username: str | None = None


class LlmConfigPage(BaseModel):
    items: list[LlmConfigView]
    page: int
    page_size: int
    total: int


class LlmConfigTestResult(BaseModel):
    success: bool
    reason_code: str
    detail: str
    http_status: int | None
    last_tested_at: str


# ----------------------------------------------------------------------
# 转换
# ----------------------------------------------------------------------


def _headers_from_specs(specs: list[LlmConfigHeaderSpec] | None) -> dict[str, str]:
    if not specs:
        return {}
    return {item.name: item.value for item in specs}


def _view(
    session: Session,
    row: LlmConfig,
    *,
    include_owner: bool = False,
) -> LlmConfigView:
    owner_username = None
    if include_owner:
        owner = session.get(User, row.owner_user_id)
        owner_username = owner.username if owner else None
    header_names: list[str] = []
    if row.headers_ciphertext:
        try:
            import json

            from ..core.config import get_settings
            from ..core.security import decrypt_secret

            decoded = decrypt_secret(
                row.headers_ciphertext,
                get_settings().app_secret,
                "llm-config",
            )
            header_names = list(json.loads(decoded).keys())
        except (ValueError, KeyError, json.JSONDecodeError):
            header_names = []
    host = urlparse(row.base_url).netloc or row.base_url
    return LlmConfigView(
        id=str(row.id),
        name=row.name,
        protocol=row.protocol.value,
        base_url=row.base_url,
        base_url_host=host,
        real_model=row.real_model,
        timeout_seconds=row.timeout_seconds,
        is_enabled=row.is_enabled,
        api_key_set=bool(row.secret_ciphertext),
        headers=[LlmConfigHeaderView(name=n, value_set=True) for n in header_names],
        last_tested_at=iso_utc(row.last_tested_at),
        last_test_result=row.last_test_result,
        created_at=iso_utc(row.created_at) or "",
        updated_at=iso_utc(row.updated_at) or "",
        owner_user_id=str(row.owner_user_id) if include_owner else None,
        owner_username=owner_username,
    )


# ----------------------------------------------------------------------
# 端点
# ----------------------------------------------------------------------


@router.get("", response_model=LlmConfigPage)
def list_llm_configs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> LlmConfigPage:
    rows, total = _service.list_for_user(
        db, user=user, page=page, page_size=page_size, search=search
    )
    include_owner = user.role is UserRole.ADMIN
    return LlmConfigPage(
        items=[_view(db, row, include_owner=include_owner) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=LlmConfigView, status_code=201)
def create_llm_config(
    payload: LlmConfigCreate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> LlmConfigView:
    row = _service.create(
        db,
        owner=user,
        name=payload.name,
        protocol=payload.protocol,
        base_url=payload.base_url,
        api_key=payload.api_key,
        model=payload.model,
        timeout_seconds=payload.timeout_seconds,
        headers=_headers_from_specs(payload.headers),
        enabled=payload.enabled,
    )
    db.commit()
    db.refresh(row)
    return _view(db, row)


def _get_config(db: Session, config_id: int, user: User) -> LlmConfig:
    return _service.get_owned(db, config_id, user)


@router.get("/{config_id}", response_model=LlmConfigView)
def get_llm_config(
    config_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> LlmConfigView:
    row = _get_config(db, config_id, user)
    return _view(db, row, include_owner=user.role is UserRole.ADMIN)


@router.patch("/{config_id}", response_model=LlmConfigView)
def update_llm_config(
    config_id: int,
    payload: LlmConfigUpdate,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> LlmConfigView:
    row = _get_config(db, config_id, user)
    fields = payload.model_dump(include=payload.model_fields_set)
    if "headers" in fields:
        fields["headers"] = _headers_from_specs(fields["headers"]) if fields["headers"] else {}
    _service.update(db, row=row, actor=user, fields=fields)
    db.commit()
    db.refresh(row)
    return _view(db, row)


@router.delete("/{config_id}", status_code=204)
def delete_llm_config(
    config_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = _get_config(db, config_id, user)
    _service.delete(db, row=row, actor=user)
    db.commit()
    return Response(status_code=204)


@router.post("/{config_id}/test", response_model=LlmConfigTestResult)
async def test_llm_config(
    config_id: int,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> LlmConfigTestResult:
    """连通性测试：使用配置自身的 base_url / api_key / headers 调用上游最小请求。

    不回显 Secret / Header 值，仅返回 success / reason_code / detail / http_status。
    """
    row, secret, headers = _service.get_owned_with_secret(db, config_id, user)
    if not row.is_enabled:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "LLM 配置已停用，无法测试",
            status_code=400,
        )
    outcome = await run_connectivity_test(
        protocol=row.protocol,
        base_url=row.base_url,
        api_key=secret,
        real_model=row.real_model,
        extra_headers=headers or None,
    )
    _service.repo.record_test_result(
        db, config_id=row.id, result="success" if outcome.success else "failed"
    )
    db.commit()
    db.refresh(row)
    return LlmConfigTestResult(
        success=outcome.success,
        reason_code=outcome.reason_code,
        detail=outcome.detail,
        http_status=outcome.http_status,
        last_tested_at=iso_utc(row.last_tested_at) or "",
    )
