from __future__ import annotations

import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ..connection_config import load_connection_config
from ..connectors.base import InboundMessage
from ..db import get_db
from ..enums import ConnectorPlatform
from ..models import IMConnection
from .deps import get_connector_manager
from .errors import ApiError, ErrorCode

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.post("/webhook/{connector_id}/inbound")
async def webhook_inbound(
    connector_id: int,
    request: Request,
    token: str = Header(default="", alias="X-Connector-Token"),
    db: Session = Depends(get_db),
    manager=Depends(get_connector_manager),
) -> dict[str, Any]:
    connection = db.get(IMConnection, connector_id)
    if (
        connection is None
        or connection.deleted_at is not None
        or connection.platform is not ConnectorPlatform.WEBHOOK
    ):
        raise ApiError(ErrorCode.NOT_FOUND, "连接不存在")
    try:
        config = load_connection_config(connection.config_json)
    except (ValueError, TypeError):
        config = {}
    expected_token = str(config.get("inbound_token", ""))
    if not token or not expected_token or not hmac.compare_digest(token, expected_token):
        raise ApiError(ErrorCode.FORBIDDEN, "连接 token 无效", status_code=403)
    payload = await request.json()
    text = str(payload.get("text", ""))
    if not text:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "text 不能为空")
    message = InboundMessage(
        connector_id=connector_id,
        sender_id=str(payload.get("sender_id", "")),
        text=text,
        conversation_id=str(payload.get("conversation_id", "")),
        external_message_id=str(payload.get("external_message_id", "")),
        reply_to_task_id=(
            str(payload["reply_to_task_id"]) if payload.get("reply_to_task_id") else None
        ),
    )
    await manager.dispatch(message)
    return {"accepted": True}


@router.websocket("/ws/{connector_id}")
async def ws_connect(
    websocket: WebSocket, connector_id: int, token: str = Query(default="")
) -> None:
    from ..db import SessionLocal

    with SessionLocal() as db:
        connection = db.get(IMConnection, connector_id)
        valid = (
            connection is not None
            and connection.deleted_at is None
            and connection.platform is ConnectorPlatform.WEBSOCKET
        )
        expected_token = ""
        if valid:
            try:
                config = load_connection_config(connection.config_json)
                expected_token = str(config.get("auth_token", ""))
            except (ValueError, TypeError):
                valid = False
    if not valid or not expected_token or not hmac.compare_digest(token, expected_token):
        await websocket.close(code=4401)
        return
    manager = websocket.app.state.connector_manager
    connector = manager.get(connector_id)
    if connector is None:
        with SessionLocal() as db:
            conn = db.get(IMConnection, connector_id)
            if conn is not None:
                await manager.configure(conn)
        connector = manager.get(connector_id)
    await websocket.accept()
    if connector is not None:
        await connector.register(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"text": raw}
            text = str(data.get("text", ""))
            if not text:
                continue
            message = InboundMessage(
                connector_id=connector_id,
                sender_id=str(data.get("sender_id", "")),
                text=text,
                conversation_id=str(data.get("conversation_id", "")),
                external_message_id=str(data.get("external_message_id", "")),
                reply_to_task_id=(
                    str(data["reply_to_task_id"]) if data.get("reply_to_task_id") else None
                ),
            )
            await manager.dispatch(message)
    except WebSocketDisconnect:
        pass
    finally:
        if connector is not None:
            await connector.unregister(websocket)
