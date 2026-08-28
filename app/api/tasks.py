from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..config import Settings, get_settings
from ..db import get_db
from ..enums import UserRole
from ..models import AdminUser, ApiKey, RequestTask
from ..schemas import HumanReplyRequest
from ..services import TaskError, TaskService, task_to_dict
from .deps import get_connector_manager, paginate, pagination_params, require_current_user
from .errors import ApiError, ErrorAction, ErrorCode

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _visible_task(db: Session, user: AdminUser, task_id: str) -> RequestTask:
    task = db.execute(
        select(RequestTask)
        .where(RequestTask.id == task_id)
        .options(joinedload(RequestTask.api_key).joinedload(ApiKey.route))
    ).scalar_one_or_none()
    if task is None:
        raise ApiError(ErrorCode.NOT_FOUND, "任务不存在")
    if user.role is not UserRole.ADMIN:
        key = db.get(ApiKey, task.api_key_id)
        if key is None or key.owner_id != user.id:
            raise ApiError(ErrorCode.FORBIDDEN, "无权查看该任务")
    return task


@router.get("")
def list_tasks(
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    params: dict = Depends(pagination_params),
    status: str | None = Query(default=None),
    api_key_id: int | None = Query(default=None),
    source: str | None = Query(default=None),
) -> dict[str, Any]:
    stmt = select(RequestTask).options(joinedload(RequestTask.api_key).joinedload(ApiKey.route))
    if user.role is not UserRole.ADMIN:
        own_keys = db.execute(select(ApiKey.id).where(ApiKey.owner_id == user.id)).scalars().all()
        stmt = stmt.where(RequestTask.api_key_id.in_(own_keys or [-1]))
    if status:
        stmt = stmt.where(RequestTask.status == status)
    if api_key_id:
        stmt = stmt.where(RequestTask.api_key_id == api_key_id)
    stmt = stmt.order_by(RequestTask.created_at.desc())
    all_tasks = list(db.execute(stmt).scalars().unique())
    total = len(all_tasks)
    start = (params["page"] - 1) * params["page_size"]
    items = []
    for t in all_tasks[start : start + params["page_size"]]:
        item = task_to_dict(t)
        key = t.api_key
        item["route_mode"] = key.route.mode.value
        item["model_name"] = key.route.model_name
        item["owner_id"] = key.owner_id
        items.append(item)
    return paginate(items, total, params)


@router.get("/{task_id}")
def task_detail(
    task_id: str, user: AdminUser = Depends(require_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    task = _visible_task(db, user, task_id)
    detail = task_to_dict(task)
    key = task.api_key
    detail["route_mode"] = key.route.mode.value
    detail["model_name"] = key.route.model_name
    detail["owner_id"] = key.owner_id
    detail["events"] = [
        {
            "sequence": e.sequence,
            "kind": e.kind.value,
            "content": e.content,
            "tool_name": e.tool_name,
            "tool_args": e.tool_args_json,
            "source": e.source.value,
            "external_message_id": e.external_message_id,
            "created_at": e.created_at.isoformat(),
        }
        for e in task.events
    ]
    return detail


@router.post("/{task_id}/reply")
def web_reply(
    task_id: str,
    payload: HumanReplyRequest,
    user: AdminUser = Depends(require_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    manager=Depends(get_connector_manager),
) -> dict[str, Any]:
    from ..enums import ReplySource

    if user.role is UserRole.ADMIN:
        raise ApiError(
            ErrorCode.FORBIDDEN,
            "管理员只能监管任务，不能代替用户回复",
            status_code=403,
        )
    _visible_task(db, user, task_id)
    service = TaskService(settings, manager)
    try:
        events = service.accept_reply(
            db, task_id, payload.text, ReplySource.WEB, user.username, payload.external_message_id
        )
    except TaskError as exc:
        raise ApiError(
            ErrorCode.CONFLICT, str(exc), status_code=409, action=ErrorAction.FIX_INPUT
        ) from exc
    return {
        "task_id": task_id,
        "accepted": True,
        "source": "web",
        "events": [e.kind.value for e in events],
    }
