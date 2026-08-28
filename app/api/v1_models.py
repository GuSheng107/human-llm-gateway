"""OpenAI 风格模型目录（GET /v1/models，docs/API_CONTRACT.md §12）。

必须使用 API Key 鉴权，按“用户可见模型 -> 模型分组 -> Key 显式选择”
计算有效集合；与三个推理入口复用同一个 effective-model 查询。
/v1/* 的 DomainError 由全局错误处理器映射为 OpenAI 兼容错误结构。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.api_keys import ApiKeyRepository
from ..repositories.models import ApiKey, User
from ..services.effective_models import EffectiveModelService
from .deps import bearer

router = APIRouter(prefix="/v1", tags=["v1"])

_service = EffectiveModelService()
_keys = ApiKeyRepository()


def require_api_key(
    request: Request,
    credentials=Depends(bearer),
    db: Session = Depends(get_db),
) -> tuple[ApiKey, User]:
    """API Key 鉴权依赖：返回 (Key, 所有者)。匿名或无效返回 401。"""
    token = credentials.credentials if credentials else None
    if not token:
        raise DomainError(DomainErrorCode.INVALID_API_KEY, "无效的 API Key", status_code=401)
    matched = _keys.authenticate(db, token)
    if matched is None:
        raise DomainError(DomainErrorCode.INVALID_API_KEY, "无效的 API Key", status_code=401)
    owner = db.get(User, matched.owner_user_id)
    if owner is None or not owner.is_active:
        raise DomainError(DomainErrorCode.INVALID_API_KEY, "无效的 API Key", status_code=401)
    _keys.touch_last_used(db, matched.id)
    return matched, owner


@router.get("/models")
def list_models(
    key_owner: tuple = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    key, _owner = key_owner
    if not key.is_enabled:
        raise DomainError(DomainErrorCode.INVALID_API_KEY, "无效的 API Key", status_code=401)
    models = _service.effective_models(db, key)
    return {
        "object": "list",
        "data": [_service.catalog_entry(row) for row in models],
    }
