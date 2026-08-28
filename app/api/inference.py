from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..dsl import ParsedEvent
from ..enums import RouteMode
from ..models import ApiKey, RequestTask
from ..protocols import (
    anthropic_json,
    anthropic_stream,
    openai_chat_json,
    openai_chat_stream,
    openai_responses_json,
    openai_responses_stream,
)
from ..services import TaskError, TaskService
from .deps import require_api_key
from .errors import ApiError, ErrorAction, ErrorCode

router = APIRouter(prefix="/v1", tags=["inference"])
PUBLIC_SERVICE_ERROR = "服务暂时不可用，请稍后重试"


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item.get("text", "")) for item in value if isinstance(item, dict))
    return str(value or "")


def openai_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "messages 不能为空")
    return [
        {"role": str(item.get("role", "user")), "content": _text_content(item.get("content"))}
        for item in messages
        if isinstance(item, dict)
    ]


def openai_responses_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_input = payload.get("input")
    if isinstance(raw_input, str) and raw_input.strip():
        return [{"role": "user", "content": raw_input}]
    if not isinstance(raw_input, list) or not raw_input:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "input 不能为空")
    messages: list[dict[str, Any]] = []
    for item in raw_input:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "message"))
        if item_type not in {"message", "input_text"}:
            continue
        content = item.get("content", item.get("text", ""))
        messages.append({"role": str(item.get("role", "user")), "content": _text_content(content)})
    if not messages:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "input 中没有可用消息")
    return messages


def anthropic_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "messages 不能为空")
    normalized: list[dict[str, Any]] = []
    system = payload.get("system")
    if system:
        normalized.append({"role": "system", "content": _text_content(system)})
    normalized.extend(
        {"role": str(item.get("role", "user")), "content": _text_content(item.get("content"))}
        for item in messages
        if isinstance(item, dict)
    )
    return normalized


def _task_service(request: Request, settings: Settings = Depends(get_settings)) -> TaskService:
    manager = getattr(request.app.state, "connector_manager", None)
    return TaskService(settings, manager)


async def _complete_task(
    task: RequestTask,
    events: list[ParsedEvent],
    payload: dict[str, Any],
    protocol: str,
    db: Session,
    service: TaskService,
) -> Any:
    def on_complete() -> None:
        service.mark_completed(db, task.id)

    if payload.get("stream"):
        if protocol == "anthropic":
            stream = anthropic_stream(task, events, get_settings(), on_complete)
        elif protocol == "openai_responses":
            stream = openai_responses_stream(task, events, get_settings(), on_complete)
        else:
            stream = openai_chat_stream(task, events, get_settings(), on_complete)
        return StreamingResponse(stream, media_type="text/event-stream")
    service.mark_completed(db, task.id)
    if protocol == "anthropic":
        body = anthropic_json(task, events)
    elif protocol == "openai_responses":
        body = openai_responses_json(task, events)
    else:
        body = openai_chat_json(task, events)
    return JSONResponse(body)


async def _run_inference(
    payload: dict[str, Any],
    key: ApiKey,
    db: Session,
    service: TaskService,
    protocol: str,
    messages: list[dict[str, Any]],
) -> Any:
    requested_model = str(payload.get("model") or key.route.model_name)
    # 客户端提交的 model 仅供 SDK 展示兼容；实际行为由绑定的 route 决定：
    # HUMAN 模式全部投递 IM/Web，LLM 模式上游模型取 route.upstream_model。
    if key.route.mode is RouteMode.LLM:
        task = service.create_llm_task(db, key, protocol, requested_model, payload)
        try:
            events = await service.complete_llm_task(db, task.id, messages)
        except TaskError as exc:
            raise ApiError(
                ErrorCode.INTERNAL_ERROR,
                PUBLIC_SERVICE_ERROR,
                status_code=500,
                action=ErrorAction.NONE,
            ) from exc
        return await _complete_task(task, events, payload, protocol, db, service)

    task = service.create_human_task(db, key, protocol, requested_model, payload)
    try:
        events = await service.await_human(task.id, key.route.human_timeout_seconds)
    except TaskError as exc:
        service.mark_timeout(db, task.id)
        if key.route.mode is not RouteMode.HUMAN_FALLBACK_LLM:
            raise ApiError(
                ErrorCode.INTERNAL_ERROR,
                PUBLIC_SERVICE_ERROR,
                status_code=500,
                action=ErrorAction.NONE,
            ) from exc
        try:
            events = await service.complete_llm_task(db, task.id, messages)
        except TaskError as llm_exc:
            raise ApiError(
                ErrorCode.INTERNAL_ERROR,
                PUBLIC_SERVICE_ERROR,
                status_code=500,
                action=ErrorAction.NONE,
            ) from llm_exc
    return await _complete_task(task, events, payload, protocol, db, service)


@router.get("/models")
def models(key: ApiKey = Depends(require_api_key), db: Session = Depends(get_db)) -> dict[str, Any]:
    from ..model_catalog import list_public_models

    return {
        "object": "list",
        "data": [
            {"id": item.model_id, "object": "model", "owned_by": item.owned_by}
            for item in list_public_models(db)
        ],
    }


@router.post("/chat/completions")
async def chat_completions(
    payload: dict[str, Any],
    request: Request,
    key: ApiKey = Depends(require_api_key),
    db: Session = Depends(get_db),
    service: TaskService = Depends(_task_service),
) -> Any:
    messages = openai_messages(payload)
    return await _run_inference(payload, key, db, service, "openai", messages)


@router.post("/messages")
async def anthropic_messages_endpoint(
    payload: dict[str, Any],
    request: Request,
    key: ApiKey = Depends(require_api_key),
    db: Session = Depends(get_db),
    service: TaskService = Depends(_task_service),
) -> Any:
    messages = anthropic_messages(payload)
    return await _run_inference(payload, key, db, service, "anthropic", messages)


@router.post("/responses")
async def responses_endpoint(
    payload: dict[str, Any],
    request: Request,
    key: ApiKey = Depends(require_api_key),
    db: Session = Depends(get_db),
    service: TaskService = Depends(_task_service),
) -> Any:
    messages = openai_responses_messages(payload)
    return await _run_inference(payload, key, db, service, "openai_responses", messages)
