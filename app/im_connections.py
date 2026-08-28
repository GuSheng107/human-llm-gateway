from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .config import Settings
from .connection_config import dump_connection_config
from .connectors.registry import ConnectorRegistry
from .enums import BindingStatus, ConnectorPlatform, ConnectorStatus, UserRole
from .models import AdminUser, AuditLog, IMConnection
from .schemas import BindingStartResponse, ConnectionCreated, ConnectionSummary
from .security import generate_binding_code, verify_binding_code

BIND_COMMAND = re.compile(r"^\s*/?(?:bind|绑定)\s+([A-Za-z0-9]+)\s*$", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(UTC)


def connection_summary(connection: IMConnection) -> ConnectionSummary:
    owner_name = connection.owner.display_name or connection.owner.username
    return ConnectionSummary(
        id=connection.id,
        name=connection.name,
        platform=connection.platform,
        status=connection.status,
        binding_status=connection.binding_status,
        owner_id=connection.owner_id,
        owner_name=owner_name,
        bound_user_id=connection.bound_user_id,
        bound_conversation_id=connection.bound_conversation_id,
        last_seen_at=connection.last_seen_at.isoformat() if connection.last_seen_at else None,
        last_error=connection.last_error,
        created_at=connection.created_at.isoformat(),
    )


def list_connections(db: Session, user: AdminUser) -> list[ConnectionSummary]:
    statement = (
        select(IMConnection)
        .where(IMConnection.deleted_at.is_(None))
        .options(joinedload(IMConnection.owner))
        .order_by(IMConnection.created_at.desc())
    )
    if user.role is not UserRole.ADMIN:
        statement = statement.where(IMConnection.owner_id == user.id)
    return [connection_summary(item) for item in db.execute(statement).scalars()]


def get_managed_connection(db: Session, user: AdminUser, connection_id: int) -> IMConnection:
    connection = db.execute(
        select(IMConnection)
        .where(IMConnection.id == connection_id, IMConnection.deleted_at.is_(None))
        .options(joinedload(IMConnection.owner))
    ).scalar_one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Bot 不存在")
    if user.role is not UserRole.ADMIN and connection.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权管理其他用户的 Bot")
    return connection


def create_user_connection(
    db: Session,
    user: AdminUser,
    *,
    name: str,
    platform: ConnectorPlatform,
    raw_config: dict[str, Any],
    registry: ConnectorRegistry,
) -> tuple[IMConnection, ConnectionCreated]:
    if user.role is UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="管理员不能创建自己的 IM Bot")
    if not name.strip():
        raise HTTPException(status_code=400, detail="Bot 名称不能为空")
    definition = registry.get(platform)
    if not definition.enabled:
        raise HTTPException(status_code=400, detail="该平台尚未开放")
    config = _normalized_config(raw_config)
    setup: dict[str, Any] = {}
    if platform is ConnectorPlatform.WEBHOOK and not config.get("inbound_token"):
        config["inbound_token"] = secrets.token_urlsafe(24)
        setup["inbound_token"] = config["inbound_token"]
    if platform is ConnectorPlatform.WEBSOCKET and not config.get("auth_token"):
        config["auth_token"] = secrets.token_urlsafe(24)
        setup["auth_token"] = config["auth_token"]
    errors = definition.validate(config)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    connection = IMConnection(
        owner=user,
        name=name.strip(),
        platform=platform,
        config_json=dump_connection_config(config),
        status=ConnectorStatus.OFFLINE,
        binding_status=BindingStatus.UNBOUND,
    )
    db.add(connection)
    db.flush()
    if platform is ConnectorPlatform.WEBHOOK:
        setup["inbound_url"] = f"/connectors/webhook/{connection.id}/inbound"
    elif platform is ConnectorPlatform.WEBSOCKET:
        setup["websocket_url"] = f"/connectors/ws/{connection.id}"
    db.add(
        AuditLog(
            action="connector.created",
            subject_type="im_connection",
            subject_id=str(connection.id),
            actor=user.username,
            detail_json=json.dumps({"platform": platform.value}, ensure_ascii=False),
        )
    )
    db.commit()
    db.refresh(connection)
    summary = connection_summary(connection)
    return connection, ConnectionCreated(**summary.model_dump(), setup=setup)


def start_binding(
    db: Session,
    user: AdminUser,
    connection: IMConnection,
    settings: Settings,
) -> BindingStartResponse:
    if user.role is UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="管理员不能绑定自己的 IM Bot")
    code, encoded = generate_binding_code()
    expires_at = utc_now() + timedelta(seconds=settings.binding_code_ttl_seconds)
    connection.binding_status = BindingStatus.BINDING
    connection.binding_code_hash = encoded
    connection.binding_expires_at = expires_at
    connection.bound_user_id = ""
    connection.bound_conversation_id = ""
    db.add(
        AuditLog(
            action="connector.binding_started",
            subject_type="im_connection",
            subject_id=str(connection.id),
            actor=user.username,
            detail_json=json.dumps({"expires_at": expires_at.isoformat()}),
        )
    )
    db.commit()
    return BindingStartResponse(
        connection_id=connection.id,
        code=code,
        command=f"/bind {code}",
        expires_at=expires_at.isoformat(),
    )


def try_complete_binding(
    db: Session,
    connection: IMConnection,
    *,
    text: str,
    sender_id: str,
    conversation_id: str,
) -> bool:
    if connection.binding_status is not BindingStatus.BINDING:
        return False
    match = BIND_COMMAND.fullmatch(text)
    if match is None:
        return False
    expires_at = connection.binding_expires_at
    if expires_at is None:
        connection.binding_status = BindingStatus.EXPIRED
        db.commit()
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < utc_now():
        connection.binding_status = BindingStatus.EXPIRED
        connection.binding_code_hash = ""
        db.commit()
        return False
    if not verify_binding_code(match.group(1), connection.binding_code_hash):
        return False
    connection.binding_status = BindingStatus.BOUND
    connection.bound_user_id = sender_id
    connection.bound_conversation_id = conversation_id or sender_id
    connection.binding_code_hash = ""
    connection.binding_expires_at = None
    connection.last_seen_at = utc_now()
    db.add(
        AuditLog(
            action="connector.bound",
            subject_type="im_connection",
            subject_id=str(connection.id),
            actor=f"im:{sender_id}",
            detail_json=json.dumps({"conversation_id": conversation_id}, ensure_ascii=False),
        )
    )
    db.commit()
    return True


def soft_delete_connection(db: Session, user: AdminUser, connection: IMConnection) -> None:
    api_key_id = connection.api_key.id if connection.api_key is not None else None
    if connection.api_key is not None:
        connection.api_key.active = False
    connection.deleted_at = utc_now()
    connection.status = ConnectorStatus.DISABLED
    connection.binding_status = BindingStatus.UNBOUND
    connection.config_json = dump_connection_config({})
    connection.bound_user_id = ""
    connection.bound_conversation_id = ""
    connection.binding_code_hash = ""
    connection.binding_expires_at = None
    connection.last_error = ""
    db.add(
        AuditLog(
            action="connector.deleted",
            subject_type="im_connection",
            subject_id=str(connection.id),
            actor=user.username,
            detail_json=json.dumps({"disabled_api_key_id": api_key_id}),
        )
    )
    db.commit()


def _normalized_config(raw: dict[str, Any]) -> dict[str, Any]:
    config = dict(raw)
    headers = config.get("headers")
    if isinstance(headers, str) and headers.strip():
        try:
            parsed = json.loads(headers)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="自定义请求头必须是 JSON 对象") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="自定义请求头必须是 JSON 对象")
        config["headers"] = parsed
    for key in ("poll_interval_seconds", "reconnect_interval_ms", "heartbeat_interval_ms"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            try:
                config[key] = float(value) if "." in value else int(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{key} 必须是数字") from exc
    return config
