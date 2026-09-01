"""连接器入口 API（/connectors/*，docs/API_CONTRACT.md §17）。

每个连接使用独立 Token 鉴权；入站消息按 connection_id +
external_message_id 全局幂等；HTTP 轮询返回单调 cursor，重复
cursor、ACK 或回复必须幂等。
"""

from __future__ import annotations

import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..connectors import connection_manager as manager
from ..connectors.implementations.ws_server import WebSocketServerConnector
from ..core.constants import MAX_CONNECTOR_WEBSOCKET_MESSAGE_BYTES
from ..core.db import SessionLocal, get_db
from ..domain.enums import InboundResult
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.connections import ConnectionRepository
from ..repositories.models import ImConnection
from ..services.connection_service import ConnectionService
from .common import StrictModel

router = APIRouter(prefix="/connectors", tags=["connectors"])

_service = ConnectionService()
_repo = ConnectionRepository()


class WebhookInbound(StrictModel):
    external_message_id: str = Field(min_length=1, max_length=255)
    sender: str = Field(default="", max_length=255)
    text: str = Field(default="", max_length=100_000)
    binding_code: str | None = Field(default=None, max_length=64)
    reply_to: str | None = Field(default=None, max_length=64)


class InboundResultBody(BaseModel):
    result: str
    task_id: str | None = None


class HttpTaskItem(BaseModel):
    task_id: str
    model: str
    prompt: str
    created_at: str
    tools: list[str]


class HttpPullResponse(BaseModel):
    cursor: int
    tasks: list[HttpTaskItem]


class HttpReplyBody(StrictModel):
    external_message_id: str = Field(min_length=1, max_length=255)
    task_id: str
    text: str = Field(min_length=1, max_length=100_000)


class HttpAckBody(StrictModel):
    task_id: str


def _get_connection(db: Session, connection_id: int) -> ImConnection:
    row = _repo.get(db, connection_id)
    if row is None:
        raise DomainError(DomainErrorCode.NOT_FOUND, "连接不存在", status_code=404)
    return row


def _verify_token(row: ImConnection, token: str | None, field: str) -> None:
    if not token:
        raise DomainError(DomainErrorCode.UNAUTHORIZED, "缺少连接凭据", status_code=401)
    config = _service.decrypt_config(row)
    expected = str(config.get(field) or "")
    if not expected or not hmac.compare_digest(expected, token):
        raise DomainError(DomainErrorCode.UNAUTHORIZED, "连接凭据无效", status_code=401)


def _result_body(result: InboundResult, task_public_id: str | None = None) -> InboundResultBody:
    return InboundResultBody(result=result.value, task_id=task_public_id)


# ---------------------------------------------------------------------------
# Webhook：提交绑定消息或任务回复
# ---------------------------------------------------------------------------


@router.post("/webhook/{connection_id}/inbound", response_model=InboundResultBody)
def webhook_inbound(
    connection_id: int,
    payload: WebhookInbound,
    x_webhook_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> InboundResultBody:
    from ..connectors.base import InboundMessage

    row = _get_connection(db, connection_id)
    _verify_token(row, x_webhook_token, "inbound_token")
    result = _service.handle_inbound(
        db,
        row=row,
        message=InboundMessage(
            external_message_id=payload.external_message_id,
            sender_external_id=payload.sender,
            text=payload.text,
            binding_code=payload.binding_code,
            reply_to_public_id=payload.reply_to,
        ),
    )
    db.commit()
    task_public_id = None
    if result in (InboundResult.ACCEPTED, InboundResult.LATE):
        task_public_id = payload.reply_to or (
            payload.text[1:].partition(" ")[0] if payload.text.startswith("#") else None
        )
    return _result_body(result, task_public_id)


# ---------------------------------------------------------------------------
# WebSocket：双向接收任务和回复
# ---------------------------------------------------------------------------


@router.websocket("/ws/{connection_id}")
async def websocket_endpoint(
    websocket: WebSocket, connection_id: int, token: str | None = Query(default=None)
) -> None:
    from ..connectors.base import InboundMessage

    # 鉴权失败必须先 accept 再关闭，避免部分客户端无法读取关闭帧。
    await websocket.accept()
    with SessionLocal() as db:
        row = _repo.get(db, connection_id)
        if row is None or row.platform != "websocket":
            await websocket.close(code=4404)
            return
        if not row.desired_running:
            await websocket.close(code=4403)
            return
        connector = manager.get_instance(connection_id)
        if not isinstance(connector, WebSocketServerConnector):
            await websocket.close(code=4403)
            return
        if not connector.verify_token(token):
            await websocket.close(code=4401)
            return

        class _SessionAdapter:
            def __init__(self, ws: WebSocket) -> None:
                self._ws = ws

            async def send_json(self, data: dict[str, Any]) -> None:
                await self._ws.send_text(json.dumps(data, ensure_ascii=False))

        session_id = await connector.register_session(_SessionAdapter(websocket))
        inbound = _service.inbound_handler()
        try:
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode("utf-8")) > MAX_CONNECTOR_WEBSOCKET_MESSAGE_BYTES:
                    await websocket.close(code=1009)
                    return
                try:
                    body = json.loads(raw)
                except ValueError:
                    await websocket.send_text(
                        json.dumps({"error": "invalid_json"}, ensure_ascii=False)
                    )
                    continue
                if not isinstance(body, dict):
                    continue
                external_id = str(body.get("external_message_id") or "")
                text = str(body.get("text") or "")
                if not external_id or not text:
                    await websocket.send_text(
                        json.dumps({"error": "missing_fields"}, ensure_ascii=False)
                    )
                    continue
                message = InboundMessage(
                    external_message_id=external_id,
                    sender_external_id=str(body.get("sender") or ""),
                    text=text,
                    binding_code=body.get("binding_code"),
                    reply_to_public_id=body.get("reply_to"),
                )
                result = await inbound(connection_id, message)
                await websocket.send_text(json.dumps({"result": result}, ensure_ascii=False))
        except WebSocketDisconnect:
            pass
        finally:
            await connector.remove_session(session_id)
    return  # ---------------------------------------------------------------------------


# HTTP 轮询：cursor 拉取任务、提交回复、可选 ACK
# ---------------------------------------------------------------------------


@router.get("/http/{connection_id}/tasks", response_model=HttpPullResponse)
def http_pull_tasks(
    connection_id: int,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
    x_pull_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> HttpPullResponse:
    row = _get_connection(db, connection_id)
    _verify_token(row, x_pull_token, "pull_token")
    if row.platform != "http_poll":
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED, "平台不支持 HTTP 轮询", status_code=400
        )
    rows = _repo.claim_outbox_batch(db, connection_id=row.id, after_cursor=cursor, limit=limit)
    items = []
    for outbox_row in rows:
        try:
            payload = json.loads(outbox_row.payload_json)
        except ValueError:
            continue
        items.append(
            HttpTaskItem(
                task_id=str(payload.get("task_id") or outbox_row.task_id),
                model=str(payload.get("model") or ""),
                prompt=str(payload.get("prompt") or ""),
                created_at=str(payload.get("created_at") or ""),
                tools=[str(name) for name in payload.get("tools") or []],
            )
        )
    new_cursor = rows[-1].id if rows else cursor
    db.commit()
    return HttpPullResponse(cursor=new_cursor, tasks=items)


@router.post("/http/{connection_id}/replies", response_model=InboundResultBody)
def http_submit_reply(
    connection_id: int,
    payload: HttpReplyBody,
    x_pull_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> InboundResultBody:
    from ..connectors.base import InboundMessage

    row = _get_connection(db, connection_id)
    _verify_token(row, x_pull_token, "pull_token")
    result = _service.handle_inbound(
        db,
        row=row,
        message=InboundMessage(
            external_message_id=payload.external_message_id,
            sender_external_id=row.bound_external_user_id or "",
            text=f"#{payload.task_id} {payload.text}",
            reply_to_public_id=payload.task_id,
        ),
    )
    db.commit()
    return _result_body(result, payload.task_id)


@router.post("/http/{connection_id}/ack")
def http_ack(
    connection_id: int,
    payload: HttpAckBody,
    x_pull_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    row = _get_connection(db, connection_id)
    _verify_token(row, x_pull_token, "pull_token")
    # task_id 是对外 public_id；ack 只对已存在 outbox 行幂等生效。
    from sqlalchemy import select

    from ..repositories.models import RequestTask

    task = db.execute(
        select(RequestTask).where(
            RequestTask.public_id == payload.task_id,
            RequestTask.owner_user_id == row.owner_user_id,
        )
    ).scalar_one_or_none()
    if task is None:
        return {"acked": False}
    acked = _repo.ack_outbox(db, row.id, task.id)
    db.commit()
    return {"acked": acked}
