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
from .connection_config import dump_connection_config, load_connection_config
from .connectors.registry import ConnectorDefinition, ConnectorRegistry
from .enums import BindingStatus, ConnectorPlatform, ConnectorStatus, UserRole
from .models import AdminUser, AuditLog, IMConnection
from .schemas import BindingStartResponse, ConnectionCreated, ConnectionSummary
from .security import generate_binding_code, verify_binding_code

BIND_COMMAND = re.compile(r"^\s*/?(?:bind|绑定)\s+([A-Za-z0-9]+)\s*$", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(UTC)


def allowed_actions(connection: IMConnection) -> list[str]:
    status = connection.status
    binding = connection.binding_status
    actions: list[str] = []
    if status in (ConnectorStatus.OFFLINE, ConnectorStatus.ERROR, ConnectorStatus.STOPPED):
        actions.append("start")
    if status in (ConnectorStatus.ONLINE, ConnectorStatus.CONNECTING):
        actions.append("stop")
    if connection.restart_required or (connection.config_version > connection.applied_version):
        actions.append("apply")
    if (
        binding in (BindingStatus.UNBOUND, BindingStatus.EXPIRED)
        and status is not ConnectorStatus.STOPPED
    ):
        actions.append("regenerate_binding")
    actions.append("view_logs")
    actions.append("delete")
    return actions


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
        error_code=connection.error_code or "",
        restart_required=connection.restart_required,
        config_version=connection.config_version,
        applied_version=connection.applied_version,
        consecutive_failures=connection.consecutive_failures,
        allowed_actions=allowed_actions(connection),
        updated_at=connection.updated_at.isoformat() if connection.updated_at else "",
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
    if connection.binding_status is BindingStatus.LOCKED:
        locked_until = connection.binding_locked_until
        if locked_until is not None:
            lu = locked_until if locked_until.tzinfo else locked_until.replace(tzinfo=UTC)
            if lu > utc_now():
                raise HTTPException(status_code=423, detail="绑定失败次数过多，请稍后重试")
    code, encoded = generate_binding_code()
    expires_at = utc_now() + timedelta(seconds=settings.binding_code_ttl_seconds)
    connection.binding_status = BindingStatus.WAITING
    connection.binding_code_hash = encoded
    connection.binding_expires_at = expires_at
    connection.bound_user_id = ""
    connection.bound_conversation_id = ""
    connection.binding_failed_attempts = 0
    connection.binding_locked_until = None
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
    if connection.binding_status is not BindingStatus.WAITING:
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
        current = (connection.binding_failed_attempts or 0) + 1
        connection.binding_failed_attempts = current
        from .services.settings_service import get_setting

        max_attempts = int(get_setting(db, "binding_max_attempts") or 5)
        if current >= max_attempts:
            connection.binding_status = BindingStatus.LOCKED
            connection.binding_locked_until = utc_now() + timedelta(
                seconds=int(get_setting(db, "circuit_breaker_cooldown_seconds") or 60)
            )
            db.commit()
        else:
            db.commit()
        return False
    connection.binding_status = BindingStatus.BOUND
    connection.bound_user_id = sender_id
    connection.bound_conversation_id = conversation_id or sender_id
    connection.binding_code_hash = ""
    connection.binding_expires_at = None
    connection.binding_failed_attempts = 0
    connection.binding_locked_until = None
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
    connection.status = ConnectorStatus.STOPPED
    connection.binding_status = BindingStatus.UNBOUND
    connection.config_json = dump_connection_config({})
    connection.bound_user_id = ""
    connection.bound_conversation_id = ""
    connection.binding_code_hash = ""
    connection.binding_expires_at = None
    connection.binding_failed_attempts = 0
    connection.binding_locked_until = None
    connection.restart_required = False
    connection.consecutive_failures = 0
    connection.error_code = ""
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


def update_connection_config(
    db: Session,
    user: AdminUser,
    connection: IMConnection,
    *,
    name: str | None,
    raw_config: dict[str, Any],
    registry: ConnectorRegistry,
) -> IMConnection:
    """合并式编辑：secret 类字段空值保留原值，版本号 +1，置 pending_restart。"""
    definition = registry.get(connection.platform)
    try:
        existing = load_connection_config(connection.config_json)
    except (ValueError, TypeError):
        existing = {}
    merged = _merge_config(existing, raw_config, definition)
    errors = definition.validate(merged)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    if name is not None and name.strip():
        connection.name = name.strip()
    connection.config_json = dump_connection_config(merged)
    connection.config_version = (connection.config_version or 1) + 1
    connection.restart_required = True
    connection.status = ConnectorStatus.PENDING_RESTART
    connection.last_error = ""
    connection.error_code = ""
    db.add(
        AuditLog(
            action="connector.config_updated",
            subject_type="im_connection",
            subject_id=str(connection.id),
            actor=user.username,
            detail_json=json.dumps(
                {"config_version": connection.config_version}, ensure_ascii=False
            ),
        )
    )
    db.commit()
    db.refresh(connection)
    return connection


def _merge_config(
    existing: dict[str, Any],
    patch: dict[str, Any],
    definition: ConnectorDefinition,
) -> dict[str, Any]:
    merged = dict(existing)
    secret_keys = {f.key for f in definition.fields if getattr(f, "secret", False)}
    secret_keys |= {
        key
        for key in list(existing.keys()) + list(patch.keys())
        if any(word in str(key).lower() for word in ("secret", "token", "password", "api_key"))
    }
    for key, value in patch.items():
        if key in secret_keys and (value is None or value == ""):
            continue
        merged[key] = value
    return _normalized_config(merged)


def mark_connection_applied(
    db: Session,
    user: AdminUser,
    connection: IMConnection,
) -> IMConnection:
    """apply 成功后由路由调用：标记 applied_version、清重启标记。"""
    connection.applied_version = connection.config_version
    connection.restart_required = False
    connection.consecutive_failures = 0
    connection.error_code = ""
    db.add(
        AuditLog(
            action="connector.config_applied",
            subject_type="im_connection",
            subject_id=str(connection.id),
            actor=user.username,
            detail_json=json.dumps(
                {"applied_version": connection.applied_version}, ensure_ascii=False
            ),
        )
    )
    db.commit()
    db.refresh(connection)
    return connection


def binding_status_snapshot(db: Session, connection: IMConnection) -> dict[str, Any]:
    """绑定状态查询：剩余时间、锁定、失败次数。"""
    from .services.settings_service import get_setting

    max_attempts = int(get_setting(db, "binding_max_attempts") or 5)
    remaining: int | None = None
    if connection.binding_expires_at:
        expires_at = connection.binding_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        remaining = max(0, int((expires_at - utc_now()).total_seconds()))
    locked_until: int | None = None
    if connection.binding_locked_until:
        lu = connection.binding_locked_until
        if lu.tzinfo is None:
            lu = lu.replace(tzinfo=UTC)
        locked_until = max(0, int((lu - utc_now()).total_seconds()))
    return {
        "binding_status": connection.binding_status.value,
        "remaining_seconds": remaining,
        "locked": connection.binding_status is BindingStatus.LOCKED,
        "locked_until_seconds": locked_until,
        "failed_attempts": connection.binding_failed_attempts or 0,
        "max_attempts": max_attempts,
    }


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
