from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..enums import UserRole
from ..llm import LLMError
from ..models import AdminUser, AuditLog, LLMModel, LLMProvider
from ..schemas import ProviderCreate, ProviderSummary
from ..security import encrypt_secret
from ..services import TaskService
from .deps import (
    get_owned_provider,
    paginate,
    pagination_params,
    require_current_user,
)
from .errors import ApiError, ErrorAction, ErrorCode

router = APIRouter(prefix="/api/providers", tags=["providers"])


def _summary(p: LLMProvider) -> ProviderSummary:
    return ProviderSummary(
        id=p.id, name=p.name, protocol=p.protocol, base_url=p.base_url, active=p.active
    )


@router.get("")
def list_providers(
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    params: dict = Depends(pagination_params),
) -> dict[str, Any]:
    stmt = select(LLMProvider).where(LLMProvider.active.is_(True))
    if user.role is not UserRole.ADMIN:
        stmt = stmt.where(LLMProvider.owner_id == user.id)
    stmt = stmt.order_by(LLMProvider.id)
    all_items = list(db.execute(stmt).scalars())
    total = len(all_items)
    start = (params["page"] - 1) * params["page_size"]
    items = [
        {
            "id": p.id,
            "name": p.name,
            "protocol": p.protocol,
            "base_url": p.base_url,
            "active": p.active,
            "owner_id": p.owner_id,
            "owner_name": p.owner.display_name or p.owner.username if p.owner else "",
        }
        for p in all_items[start : start + params["page_size"]]
    ]
    return paginate(items, total, params)


@router.post("", response_model=ProviderSummary)
def create_provider(
    payload: ProviderCreate,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_current_user),
) -> ProviderSummary:
    if payload.protocol not in {"openai_compatible", "anthropic"}:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED, "供应商协议只支持 openai_compatible 或 anthropic"
        )
    existing = db.execute(
        select(LLMProvider).where(
            LLMProvider.name == payload.name,
            LLMProvider.owner_id == user.id,
            LLMProvider.active.is_(True),
        )
    ).scalar_one_or_none()
    if existing:
        raise ApiError(ErrorCode.CONFLICT, "供应商名称已存在", status_code=409)
    provider = LLMProvider(
        name=payload.name,
        protocol=payload.protocol,
        base_url=payload.base_url.rstrip("/"),
        api_key_encrypted=encrypt_secret(payload.api_key, get_settings().app_secret)
        if payload.api_key
        else "",
        options_json=json.dumps(payload.options, ensure_ascii=False),
        owner_id=user.id,
    )
    db.add(provider)
    try:
        db.flush()
        db.add(
            AuditLog(
                action="provider.created",
                subject_type="llm_provider",
                subject_id=str(provider.id),
                actor=user.username,
                detail_json=json.dumps({"name": provider.name}, ensure_ascii=False),
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise ApiError(ErrorCode.CONFLICT, "供应商名称已存在或配置无效", status_code=409) from exc
    return _summary(provider)


@router.get("/{provider_id}")
def get_provider(
    provider_id: int, user: AdminUser = Depends(require_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    p = get_owned_provider(db, user, provider_id)
    return {
        "id": p.id,
        "name": p.name,
        "protocol": p.protocol,
        "base_url": p.base_url,
        "active": p.active,
        "owner_id": p.owner_id,
        "has_api_key": bool(p.api_key_encrypted),
        "options": json.loads(p.options_json or "{}"),
    }


@router.post("/{provider_id}/update", response_model=ProviderSummary)
def update_provider(
    provider_id: int,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_current_user),
) -> ProviderSummary:
    p = get_owned_provider(db, user, provider_id)
    if payload.get("name"):
        p.name = payload["name"]
    if payload.get("protocol"):
        p.protocol = payload["protocol"]
    if payload.get("base_url"):
        p.base_url = str(payload["base_url"]).rstrip("/")
    if payload.get("api_key"):
        p.api_key_encrypted = encrypt_secret(str(payload["api_key"]), get_settings().app_secret)
    if "options" in payload:
        p.options_json = json.dumps(payload["options"], ensure_ascii=False)
    db.commit()
    return _summary(p)


@router.post("/{provider_id}/delete")
def delete_provider(
    provider_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_current_user)
) -> dict[str, Any]:
    p = get_owned_provider(db, user, provider_id)
    p.active = False
    db.add(
        AuditLog(
            action="provider.deleted",
            subject_type="llm_provider",
            subject_id=str(p.id),
            actor=user.username,
            detail_json="{}",
        )
    )
    db.commit()
    return {"deleted": True}


@router.post("/{provider_id}/validate")
async def validate_provider(
    provider_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_current_user)
) -> dict[str, Any]:
    p = get_owned_provider(db, user, provider_id)
    if not p.api_key_encrypted:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "供应商未配置 API Key")
    try:
        models = await TaskService(get_settings()).llm.list_models(p, get_settings().app_secret)
        return {"valid": True, "model_count": len(models)}
    except LLMError as exc:
        return {"valid": False, "error": str(exc)}


@router.post("/{provider_id}/models/sync")
async def sync_provider_models(
    provider_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_current_user)
) -> dict[str, Any]:
    p = get_owned_provider(db, user, provider_id)
    try:
        models = await TaskService(get_settings()).llm.list_models(p, get_settings().app_secret)
    except LLMError as exc:
        raise ApiError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "获取上游模型列表失败",
            status_code=502,
            action=ErrorAction.CONTACT_ADMIN,
            details={"error": str(exc)},
        ) from exc
    for item in models:
        model_id = str(item["id"])
        catalog = db.execute(
            select(LLMModel).where(LLMModel.provider_id == p.id, LLMModel.model_id == model_id)
        ).scalar_one_or_none()
        if catalog is None:
            catalog = LLMModel(provider_id=p.id, model_id=model_id)
            db.add(catalog)
        catalog.owned_by = str(item.get("owned_by", ""))
        catalog.metadata_json = json.dumps(item, ensure_ascii=False)
        catalog.active = True
    db.add(
        AuditLog(
            action="provider.models_synced",
            subject_type="llm_provider",
            subject_id=str(p.id),
            actor=user.username,
            detail_json=json.dumps({"count": len(models)}, ensure_ascii=False),
        )
    )
    db.commit()
    return {
        "object": "list",
        "provider_id": p.id,
        "data": [
            {
                "id": item["id"],
                "object": item.get("object", "model"),
                "owned_by": item.get("owned_by", ""),
            }
            for item in models
        ],
    }


@router.get("/{provider_id}/models")
def list_provider_models(
    provider_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_current_user)
) -> dict[str, Any]:
    p = get_owned_provider(db, user, provider_id)
    models = db.execute(
        select(LLMModel)
        .where(LLMModel.provider_id == p.id, LLMModel.active.is_(True))
        .order_by(LLMModel.model_id)
    ).scalars()
    return {
        "object": "list",
        "provider_id": p.id,
        "data": [
            {"id": item.model_id, "object": "model", "owned_by": item.owned_by} for item in models
        ],
    }
