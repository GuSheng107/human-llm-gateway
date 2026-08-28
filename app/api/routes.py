from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import RouteMode, UserRole
from ..model_catalog import list_public_models
from ..models import AdminUser, AuditLog, LLMModel, LLMProvider, ModelRoute, PublicModel
from ..schemas import PublicModelCreate, PublicModelSummary, RouteCreate, RouteSummary
from .deps import get_owned_route, paginate, pagination_params, require_current_user
from .errors import ApiError, ErrorAction, ErrorCode

router = APIRouter(prefix="/api", tags=["routes"])


def _route_summary(r: ModelRoute) -> RouteSummary:
    try:
        allowed = json.loads(r.allowed_models_json or "[]")
    except json.JSONDecodeError:
        allowed = []
    names = [str(n) for n in allowed if str(n).strip()] if isinstance(allowed, list) else []
    return RouteSummary(
        id=r.id,
        name=r.name,
        model_name=r.model_name,
        upstream_model=r.upstream_model,
        model_names=list(dict.fromkeys([r.model_name, *names])),
        mode=r.mode,
        provider_id=r.provider_id,
        human_timeout_seconds=r.human_timeout_seconds,
    )


def _validate_route_payload(
    db: Session, payload: dict[str, Any], *, route_id: int | None = None
) -> None:
    mode = payload.get("mode")
    provider_id = payload.get("provider_id")
    if mode in {RouteMode.LLM.value, RouteMode.HUMAN_FALLBACK_LLM.value} and not provider_id:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED, "LLM 或 fallback 路由必须配置供应商", status_code=422
        )
    if provider_id is not None:
        provider = db.get(LLMProvider, provider_id)
        if provider is None or not provider.active:
            raise ApiError(ErrorCode.NOT_FOUND, "LLM 供应商不存在")
        upstream = payload.get("upstream_model") or ""
        if upstream:
            synced = db.execute(
                select(LLMModel).where(
                    LLMModel.provider_id == provider_id,
                    LLMModel.model_id == upstream,
                    LLMModel.active.is_(True),
                )
            ).scalar_one_or_none()
            if synced is None:
                raise ApiError(
                    ErrorCode.VALIDATION_FAILED,
                    "上游模型不在该供应商已同步列表中，请先同步模型",
                    status_code=422,
                )
    model_name = payload.get("model_name") or ""
    if model_name:
        entry = db.execute(
            select(PublicModel).where(
                PublicModel.model_id == model_name, PublicModel.active.is_(True)
            )
        ).scalar_one_or_none()
        if entry is None:
            raise ApiError(
                ErrorCode.VALIDATION_FAILED,
                "对外模型名必须来自管理员维护的模型目录",
                status_code=422,
            )


@router.get("/model-routes")
def list_routes(
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    params: dict = Depends(pagination_params),
) -> dict[str, Any]:
    stmt = select(ModelRoute)
    if user.role is not UserRole.ADMIN:
        stmt = stmt.where(ModelRoute.owner_id == user.id)
    stmt = stmt.order_by(ModelRoute.id)
    all_routes = list(db.execute(stmt).scalars())
    total = len(all_routes)
    start = (params["page"] - 1) * params["page_size"]
    items = []
    for r in all_routes[start : start + params["page_size"]]:
        summary = _route_summary(r).model_dump()
        summary["owner_id"] = r.owner_id
        provider = db.get(LLMProvider, r.provider_id) if r.provider_id else None
        summary["provider_name"] = provider.name if provider else ""
        items.append(summary)
    return paginate(items, total, params)


@router.post("/model-routes", response_model=RouteSummary)
def create_route(
    payload: RouteCreate,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_current_user),
) -> RouteSummary:
    _validate_route_payload(db, payload.model_dump())
    route = ModelRoute(
        name=payload.name,
        model_name=payload.model_name,
        upstream_model=payload.upstream_model or payload.model_name,
        mode=payload.mode,
        provider_id=payload.provider_id,
        owner_id=user.id,
        human_timeout_seconds=payload.human_timeout_seconds,
        allowed_models_json=json.dumps(
            list(dict.fromkeys([payload.model_name, *payload.model_names])), ensure_ascii=False
        ),
    )
    db.add(route)
    db.flush()
    db.add(
        AuditLog(
            action="route.created",
            subject_type="model_route",
            subject_id=str(route.id),
            actor=user.username,
            detail_json=json.dumps({"model_name": route.model_name}, ensure_ascii=False),
        )
    )
    db.commit()
    return _route_summary(route)


@router.get("/model-routes/{route_id}")
def get_route(
    route_id: int, user: AdminUser = Depends(require_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    r = get_owned_route(db, user, route_id)
    summary = _route_summary(r).model_dump()
    summary["owner_id"] = r.owner_id
    return summary


@router.post("/model-routes/{route_id}/update", response_model=RouteSummary)
def update_route(
    route_id: int,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_current_user),
) -> RouteSummary:
    r = get_owned_route(db, user, route_id)
    merged = {
        "mode": payload.get("mode", r.mode.value),
        "provider_id": payload.get("provider_id", r.provider_id),
        "upstream_model": payload.get("upstream_model", r.upstream_model),
        "model_name": payload.get("model_name", r.model_name),
    }
    _validate_route_payload(db, merged)
    if payload.get("name"):
        r.name = payload["name"]
    if "model_name" in payload:
        r.model_name = payload["model_name"]
    if "upstream_model" in payload:
        r.upstream_model = payload["upstream_model"] or r.model_name
    if "mode" in payload:
        r.mode = RouteMode(payload["mode"])
    if "provider_id" in payload:
        r.provider_id = payload["provider_id"]
    if "human_timeout_seconds" in payload:
        r.human_timeout_seconds = int(payload["human_timeout_seconds"])
    db.add(
        AuditLog(
            action="route.updated",
            subject_type="model_route",
            subject_id=str(r.id),
            actor=user.username,
            detail_json="{}",
        )
    )
    db.commit()
    return _route_summary(r)


@router.post("/model-routes/{route_id}/delete")
def delete_route(
    route_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_current_user)
) -> dict[str, Any]:
    r = get_owned_route(db, user, route_id)
    if r.api_key is not None:
        raise ApiError(
            ErrorCode.CONFLICT,
            "路由已被 API Key 绑定，请先删除对应 Key",
            status_code=409,
            action=ErrorAction.FIX_INPUT,
        )
    db.add(
        AuditLog(
            action="route.deleted",
            subject_type="model_route",
            subject_id=str(r.id),
            actor=user.username,
            detail_json="{}",
        )
    )
    db.delete(r)
    db.commit()
    return {"deleted": True}


# ── 对外模型目录（public_models，管理员维护） ───────────────────────────


@router.get("/model-catalog")
def list_catalog(
    user: AdminUser = Depends(require_current_user), db: Session = Depends(get_db)
) -> list[PublicModelSummary]:
    include_inactive = user.role is UserRole.ADMIN
    items = list_public_models(db, include_inactive=include_inactive)
    return [
        PublicModelSummary(
            id=i.id,
            model_id=i.model_id,
            owned_by=i.owned_by,
            sort_order=i.sort_order,
            active=i.active,
        )
        for i in items
    ]


@router.post("/model-catalog", response_model=PublicModelSummary)
def create_catalog_entry(
    payload: PublicModelCreate,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_current_user),
) -> PublicModelSummary:
    _require_admin_user(admin)
    if db.execute(
        select(PublicModel).where(PublicModel.model_id == payload.model_id)
    ).scalar_one_or_none():
        raise ApiError(ErrorCode.CONFLICT, "模型 ID 已存在", status_code=409)
    item = PublicModel(
        model_id=payload.model_id,
        owned_by=payload.owned_by,
        sort_order=payload.sort_order,
        active=payload.active,
    )
    db.add(item)
    db.flush()
    db.add(
        AuditLog(
            action="public_model.created",
            subject_type="public_model",
            subject_id=str(item.id),
            actor=admin.username,
            detail_json=json.dumps({"model_id": item.model_id}, ensure_ascii=False),
        )
    )
    db.commit()
    return PublicModelSummary(
        id=item.id,
        model_id=item.model_id,
        owned_by=item.owned_by,
        sort_order=item.sort_order,
        active=item.active,
    )


@router.post("/model-catalog/{entry_id}/update", response_model=PublicModelSummary)
def update_catalog_entry(
    entry_id: int,
    payload: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_current_user),
) -> PublicModelSummary:
    _require_admin_user(user)
    item = db.get(PublicModel, entry_id)
    if item is None:
        raise ApiError(ErrorCode.NOT_FOUND, "公开模型不存在")
    new_model_id = payload.get("model_id")
    if new_model_id and new_model_id != item.model_id:
        if db.execute(
            select(PublicModel).where(PublicModel.model_id == new_model_id)
        ).scalar_one_or_none():
            raise ApiError(ErrorCode.CONFLICT, "模型 ID 已存在", status_code=409)
        item.model_id = new_model_id
    if "owned_by" in payload:
        item.owned_by = payload["owned_by"]
    if "sort_order" in payload:
        item.sort_order = int(payload["sort_order"])
    if "active" in payload:
        item.active = bool(payload["active"])
    db.add(
        AuditLog(
            action="public_model.updated",
            subject_type="public_model",
            subject_id=str(item.id),
            actor=user.username,
            detail_json=json.dumps(payload, ensure_ascii=False),
        )
    )
    db.commit()
    return PublicModelSummary(
        id=item.id,
        model_id=item.model_id,
        owned_by=item.owned_by,
        sort_order=item.sort_order,
        active=item.active,
    )


@router.post("/model-catalog/{entry_id}/delete")
def delete_catalog_entry(
    entry_id: int, db: Session = Depends(get_db), user: AdminUser = Depends(require_current_user)
) -> dict[str, Any]:
    _require_admin_user(user)
    item = db.get(PublicModel, entry_id)
    if item is None:
        raise ApiError(ErrorCode.NOT_FOUND, "公开模型不存在")
    db.add(
        AuditLog(
            action="public_model.deleted",
            subject_type="public_model",
            subject_id=str(item.id),
            actor=user.username,
            detail_json=json.dumps({"model_id": item.model_id}, ensure_ascii=False),
        )
    )
    db.delete(item)
    db.commit()
    return {"deleted": True}


def _require_admin_user(user: AdminUser) -> None:
    if user.role is not UserRole.ADMIN:
        raise ApiError(ErrorCode.FORBIDDEN, "只有管理员可以维护模型目录", status_code=403)
