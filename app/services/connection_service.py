"""IM 连接用例：加密配置、生命周期、绑定与进站处理。

配置整体认证加密（purpose=im-config）；修改 Secret 时空值保留原值。
管理员可治理（查看/检查/启停/删除）全部用户连接，但不能创建或绑定。
进站消息按 connection_id + external_message_id 全局幂等。
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import io
import json
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..connectors.base import Connector, InboundMessage
from ..connectors.registry import ConnectorRegistry, default_registry
from ..core.config import get_settings
from ..core.constants import BINDING_CODE_TTL_FALLBACK_SECONDS
from ..core.db import begin_immediate_if_sqlite
from ..core.logging import get_request_id
from ..core.security import (
    decrypt_secret,
    encrypt_secret,
    generate_im_connection_token,
    hash_binding_code,
    is_im_connection_token,
    verify_binding_code,
)
from ..core.time import utc_now
from ..domain.connections import ConnectorError
from ..domain.dsl import is_empty_draft, parse_reply
from ..domain.enums import (
    ActorType,
    AuditAction,
    ConnectionState,
    InboundResult,
    TaskEventType,
    UserRole,
)
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.connections import ConnectionRepository
from ..repositories.models import ImConnection, RequestTask, User
from ..repositories.system import AuditRepository
from ..repositories.tasks import TaskRepository

_IM_CONFIG_PURPOSE = "im-config"


class ConnectionService:
    def __init__(self, registry: ConnectorRegistry = default_registry) -> None:
        self.registry = registry
        self.repo = ConnectionRepository()
        self.audit = AuditRepository()
        self.tasks = TaskRepository()
        # 扫码登录会话：connection_id -> 登录连接器实例（跨 start/poll 请求共享）。
        self._login_connectors: dict[int, Connector] = {}

    # ------------------------------------------------------------------
    # 配置加密
    # ------------------------------------------------------------------

    @staticmethod
    def _encrypt_config(config: dict[str, Any]) -> str:
        encoded = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
        return encrypt_secret(encoded, get_settings().app_secret, _IM_CONFIG_PURPOSE)

    @staticmethod
    def decrypt_config(row: ImConnection) -> dict[str, Any]:
        if row.config_key_version != 1:
            raise DomainError(DomainErrorCode.CONFLICT, "连接配置加密版本不受支持", status_code=409)
        try:
            encoded = decrypt_secret(
                row.config_ciphertext, get_settings().app_secret, _IM_CONFIG_PURPOSE
            )
        except Exception as exc:
            raise DomainError(
                DomainErrorCode.CONFLICT, "连接配置解密失败，请重新保存配置", status_code=409
            ) from exc
        return json.loads(encoded)

    def _public_config(self, row: ImConnection) -> dict[str, Any]:
        """返回脱敏配置：Secret 字段不出现在任何响应中。"""
        spec = self.registry.get_spec(row.platform)
        config = self.decrypt_config(row)
        result: dict[str, Any] = {}
        for field in spec.config_fields if spec else []:
            if not field.user_configurable:
                continue
            value = config.get(field.name)
            if field.secret:
                result[field.name] = None  # 只表示"已设置"
                result[f"{field.name}_set"] = bool(value)
            else:
                result[field.name] = value
        return result

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, session: Session, connection_id: int) -> ImConnection:
        row = self.repo.get(session, connection_id)
        if row is None:
            raise DomainError(DomainErrorCode.NOT_FOUND, "连接不存在", status_code=404)
        return row

    def get_owned(self, session: Session, connection_id: int, owner_user_id: int) -> ImConnection:
        row = self.repo.get_owned(session, connection_id, owner_user_id)
        if row is None:
            raise DomainError(DomainErrorCode.NOT_FOUND, "连接不存在", status_code=404)
        return row

    @staticmethod
    def _reject_manual_gateway_tokens(spec, config: dict[str, Any]) -> None:
        """网关自签 Token 不允许手填：只能自动生成或通过 rotate 换新。"""
        for field in spec.gateway_token_fields():
            value = config.get(field.name)
            if value is not None and value != "":
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    f"{field.label} 由网关自动生成，不允许手动填写；如需更换请使用重新生成操作",
                    status_code=400,
                )

    def _generate_gateway_tokens(self, spec, config: dict[str, Any]) -> dict[str, str]:
        """为留空的 gateway_token 字段生成 Token，返回明文（仅本次响应展示）。"""
        generated: dict[str, str] = {}
        for field in spec.gateway_token_fields():
            if not config.get(field.name):
                token = generate_im_connection_token()
                config[field.name] = token
                generated[field.name] = token
        return generated

    def create(
        self,
        session: Session,
        *,
        owner: User,
        name: str,
        platform: str,
        config: dict[str, Any],
        actor_user_id: int | None = None,
    ) -> tuple[ImConnection, dict[str, str]]:
        if owner.role is UserRole.ADMIN:
            raise DomainError(DomainErrorCode.FORBIDDEN, "管理员不能创建用户连接", status_code=403)
        name = (name or "").strip()
        if not name or len(name) > 100:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "连接名称不能为空且最多 100 字符",
                status_code=400,
            )
        spec = self.registry.get_spec(platform)
        if spec is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "不支持的连接平台", status_code=400
            )
        unsupported_fields = set(config) - spec.user_config_field_names()
        if unsupported_fields:
            message = (
                "微信 iLink 只支持扫码绑定，无需填写连接信息"
                if spec.supports_login
                else f"存在不可配置字段: {', '.join(sorted(unsupported_fields))}"
            )
            raise DomainError(DomainErrorCode.VALIDATION_FAILED, message, status_code=400)
        self._reject_manual_gateway_tokens(spec, config)
        final_config = dict(config)
        generated_tokens = self._generate_gateway_tokens(spec, final_config)
        problems = self.registry.validate_config(platform, final_config)
        if problems:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "; ".join(problems), status_code=400
            )
        begin_immediate_if_sqlite(session)
        if self.repo.get_by_owner_platform(session, owner.id, platform) is not None:
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "每个平台只能创建一条连接",
                status_code=409,
            )
        row = ImConnection(
            owner_user_id=owner.id,
            name=name,
            platform=platform,
            config_ciphertext=self._encrypt_config(final_config),
            config_key_version=1,
            desired_running=False,
            state=ConnectionState.STOPPED,
        )
        self.repo.add(session, row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DomainError(
                DomainErrorCode.CONFLICT,
                "每个平台只能创建一条连接",
                status_code=409,
            ) from exc
        self.audit.add(
            session,
            action=AuditAction.CONNECTION_CREATED,
            resource_type="im_connection",
            resource_id=str(row.id),
            actor_user_id=actor_user_id or owner.id,
            owner_user_id=owner.id,
            metadata={"fields": ["name", "platform"]},
        )
        return row, generated_tokens

    def update(
        self,
        session: Session,
        *,
        row: ImConnection,
        actor_user_id: int,
        name: str | None = None,
        config_changes: dict[str, Any] | None = None,
    ) -> ImConnection:
        """修改名称或配置。

        Secret 字段语义：省略或空值保留原值，显式提交新值才替换
        （docs/ROADMAP.md M4：修改 Secret 时空值保留原值）。
        网关自签 Token（gateway_token）额外约束：不允许手填新值，
        只能留空保留原值或通过 rotate 换新。
        """
        spec = self.registry.require_spec(row.platform)
        merged = self.decrypt_config(row)
        changed_fields: list[str] = []
        if config_changes:
            unsupported_fields = set(config_changes) - spec.user_config_field_names()
            if unsupported_fields:
                message = (
                    "微信 iLink 参数由扫码绑定自动保存，不能手工修改"
                    if spec.supports_login
                    else f"存在不可配置字段: {', '.join(sorted(unsupported_fields))}"
                )
                raise DomainError(DomainErrorCode.VALIDATION_FAILED, message, status_code=400)
            self._reject_manual_gateway_tokens(spec, config_changes)
            for field in spec.config_fields:
                if field.name not in config_changes:
                    continue
                value = config_changes[field.name]
                if value is None or (isinstance(value, str) and value == ""):
                    continue  # 空值保留原值
                merged[field.name] = value
                changed_fields.append(field.name)
            problems = self.registry.validate_config(row.platform, merged)
            if problems:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED, "; ".join(problems), status_code=400
                )
            row.config_ciphertext = self._encrypt_config(merged)
            changed_fields = sorted(set(changed_fields))
        if name is not None:
            new_name = name.strip()
            if not new_name or len(new_name) > 100:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "连接名称不能为空且最多 100 字符",
                    status_code=400,
                )
            if new_name != row.name:
                row.name = new_name
                changed_fields.append("name")
        # 配置变化后旧的登录会话（持有旧 token 的 SDK client）不再可信。
        if changed_fields:
            self._login_connectors.pop(row.id, None)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DomainError(DomainErrorCode.CONFLICT, "同名连接已存在", status_code=409) from exc
        if changed_fields:
            self.audit.add(
                session,
                action=AuditAction.CONNECTION_UPDATED,
                resource_type="im_connection",
                resource_id=str(row.id),
                actor_user_id=actor_user_id,
                owner_user_id=row.owner_user_id,
                metadata={"fields": changed_fields},
            )
        return row

    async def rotate_credential(
        self, session: Session, *, row: ImConnection, field_name: str, actor_user_id: int
    ) -> tuple[ImConnection, str]:
        """为网关自签 Token 字段原子生成并保存新 Token。

        - 仅连接所有者可调用（API 层校验）。
        - 明文 Token 只出现在本次返回值，不写入日志和审计 metadata。
        - 连接已启用时只重启目标连接（apply 语义），未启用则仅保存。
        """
        spec = self.registry.require_spec(row.platform)
        field = next((f for f in spec.gateway_token_fields() if f.name == field_name), None)
        if field is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "该字段不是网关自签 Token，不支持重新生成",
                status_code=400,
            )
        config = self.decrypt_config(row)
        token = generate_im_connection_token()
        config[field_name] = token
        row.config_ciphertext = self._encrypt_config(config)
        self._login_connectors.pop(row.id, None)
        await run_in_threadpool(session.flush)
        if row.desired_running:
            from ..connectors import connection_manager as manager

            await manager.stop(row.id)
            await manager.start(row, config, self.inbound_handler())
            await run_in_threadpool(session.refresh, row)
        self.audit.add(
            session,
            action=AuditAction.CONNECTION_UPDATED,
            resource_type="im_connection",
            resource_id=str(row.id),
            actor_user_id=actor_user_id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": [field_name], "note": "credential_rotated"},
        )
        return row, token

    async def delete(self, session: Session, *, row: ImConnection, actor_user_id: int) -> None:

        total, prefixes = await run_in_threadpool(
            self.repo.count_api_key_references, session, row.id, enabled_only=False
        )
        if total:
            hint = (
                f"连接仍被 {total} 个 API Key 引用（如 {', '.join(prefixes[:5])}），"
                "请先在 API Key 中改用其他 IM 连接或 Web 入口"
            )
            raise DomainError(
                DomainErrorCode.CONFLICT,
                hint,
                status_code=409,
            )
        # 先停止运行中的连接器，避免删除后线程/长连接泄漏并继续接收入站消息。
        from ..connectors import connection_manager as manager

        await manager.stop(row.id)
        self._login_connectors.pop(row.id, None)
        row.desired_running = False
        await run_in_threadpool(session.flush)
        await run_in_threadpool(self.repo.delete, session, row.id)
        self.audit.add(
            session,
            action=AuditAction.CONNECTION_DELETED,
            resource_type="im_connection",
            resource_id=str(row.id),
            actor_user_id=actor_user_id,
            owner_user_id=row.owner_user_id,
        )

    # ------------------------------------------------------------------
    # 生命周期（用户与管理员治理共用；只影响目标连接）
    # ------------------------------------------------------------------

    async def start(
        self, session: Session, *, row: ImConnection, actor_user_id: int
    ) -> ImConnection:
        from ..connectors import connection_manager as manager

        spec = self.registry.require_spec(row.platform)
        if spec.requires_binding and row.bound_external_user_id is None:
            action = "扫码登录" if spec.supports_login else "完成用户绑定"
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"请先{action}，绑定成功后才能启用连接",
                status_code=400,
            )
        # 启用前状态校验：处于异常/未绑定态不允许直接启用，避免开即坏。
        if row.state in (ConnectionState.AUTH_REQUIRED, ConnectionState.ERROR):
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"连接当前状态异常（{row.state.value}），请先修复后再启用",
                status_code=400,
            )
        if row.state is not ConnectionState.ONLINE and row.desired_running:
            pass  # 已处于启用流程中的中间态（starting 等）允许继续
        config = self.decrypt_config(row)
        # required_at_runtime 的网关自签 Token 必须齐备且格式合法，
        # 防止历史/异常数据把缺 Token 的连接器拉起。
        for field in spec.gateway_token_fields():
            if field.required_at_runtime and not is_im_connection_token(config.get(field.name)):
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    f"{field.label} 缺失或无效，请先重新生成接入 Token",
                    status_code=400,
                )
        row.desired_running = True
        await run_in_threadpool(session.flush)
        await manager.start(row, config, self.inbound_handler())
        await run_in_threadpool(session.refresh, row)
        self.audit.add(
            session,
            action=AuditAction.CONNECTION_STARTED,
            resource_type="im_connection",
            resource_id=str(row.id),
            actor_user_id=actor_user_id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["desired_running"]},
        )
        return row

    async def stop(
        self, session: Session, *, row: ImConnection, actor_user_id: int
    ) -> ImConnection:
        from ..connectors import connection_manager as manager

        row.desired_running = False
        row.state = ConnectionState.STOPPED
        row.next_retry_at = None
        await run_in_threadpool(session.flush)
        await manager.stop(row.id)
        await run_in_threadpool(session.refresh, row)
        self.audit.add(
            session,
            action=AuditAction.CONNECTION_STOPPED,
            resource_type="im_connection",
            resource_id=str(row.id),
            actor_user_id=actor_user_id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["desired_running", "state"]},
        )
        return row

    async def apply(
        self, session: Session, *, row: ImConnection, actor_user_id: int
    ) -> ImConnection:
        """应用保存的配置并只重启目标连接。"""
        from ..connectors import connection_manager as manager

        await manager.stop(row.id)
        if row.desired_running:
            await manager.start(row, self.decrypt_config(row), self.inbound_handler())
            await run_in_threadpool(session.refresh, row)
        else:
            row.state = ConnectionState.STOPPED
            await run_in_threadpool(session.flush)
            await run_in_threadpool(session.refresh, row)
        self.audit.add(
            session,
            action=AuditAction.CONNECTION_APPLIED,
            resource_type="im_connection",
            resource_id=str(row.id),
            actor_user_id=actor_user_id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["config"]},
        )
        return row

    def health(self, session: Session, row: ImConnection) -> dict[str, Any]:
        from ..connectors import connection_manager as manager

        connector = manager.get_instance(row.id)
        runtime: dict[str, Any] = {"running": connector is not None}
        if connector is not None:
            info = connector.health()
            if inspect.isawaitable(info):
                info = _run_coroutine(info)
            runtime.update(info)
        return {
            "state": row.state.value,
            "desired_running": row.desired_running,
            "retry_count": row.retry_count,
            "next_retry_at": row.next_retry_at.isoformat() if row.next_retry_at else None,
            "last_authenticated_at": (
                row.last_authenticated_at.isoformat() if row.last_authenticated_at else None
            ),
            "last_health_at": row.last_health_at.isoformat() if row.last_health_at else None,
            "last_error_code": row.last_error_code,
            "last_error_message": row.last_error_message,
            "runtime": runtime,
        }

    # ------------------------------------------------------------------
    # 绑定与登录（仅所有者）
    # ------------------------------------------------------------------

    def create_binding_code(
        self, session: Session, *, row: ImConnection, actor_user_id: int
    ) -> dict[str, Any]:
        spec = self.registry.require_spec(row.platform)
        code = spec.binding_command
        if code is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "该平台不使用消息绑定",
                status_code=400,
            )
        code_hash = hash_binding_code(code)
        ttl = getattr(get_settings(), "binding_code_ttl_seconds", BINDING_CODE_TTL_FALLBACK_SECONDS)
        expires_at = utc_now() + timedelta(seconds=ttl)
        self.repo.set_binding_code(session, row.id, code_hash, expires_at)
        self.audit.add(
            session,
            action=AuditAction.CONNECTION_UPDATED,
            resource_type="im_connection",
            resource_id=str(row.id),
            actor_user_id=actor_user_id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["binding_code"]},
        )
        return {"binding_code": code, "expires_at": expires_at.isoformat() + "Z"}

    async def start_binding_listener(self, row: ImConnection) -> None:
        """为主动连接平台启动临时监听，等待用户发送固定绑定命令。

        该监听不改变 ``desired_running``。绑定成功后进站回调会在开关仍关闭时
        停止监听；绑定码过期时由看门狗停止，避免形成独立的“停用”状态。
        """
        spec = self.registry.require_spec(row.platform)
        if spec.kind != "client" or spec.binding_command is None:
            return
        from ..connectors import connection_manager as manager

        await manager.start(row, self.decrypt_config(row), self.inbound_handler())

    async def cancel_binding_listener(
        self, session: Session, *, row: ImConnection, actor_user_id: int
    ) -> ImConnection:
        """取消本次绑定监听：停实例 + 清绑定码；连接与凭据全部保留。"""
        from ..connectors import connection_manager as manager

        if row.desired_running:
            # 已启用的连接不受影响：绑定码过期自然清理。
            self.repo.set_binding_code(session, row.id, None, None)
            await run_in_threadpool(session.flush)
            return row
        await manager.stop(row.id)
        self._login_connectors.pop(row.id, None)
        row.state = ConnectionState.STOPPED
        row.next_retry_at = None
        self.repo.set_binding_code(session, row.id, None, None)
        await run_in_threadpool(session.flush)
        await run_in_threadpool(session.refresh, row)
        self.audit.add(
            session,
            action=AuditAction.CONNECTION_UPDATED,
            resource_type="im_connection",
            resource_id=str(row.id),
            actor_user_id=actor_user_id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["binding_code"], "note": "binding_cancelled"},
        )
        return row

    def binding_status(self, session: Session, row: ImConnection) -> dict[str, Any]:
        pending = row.binding_code_hash is not None and (
            row.binding_code_expires_at is None or row.binding_code_expires_at > utc_now()
        )
        return {
            "bound": row.bound_external_user_id is not None,
            "binding_pending": pending,
            "binding_expires_at": (
                row.binding_code_expires_at.isoformat() + "Z"
                if row.binding_code_expires_at and pending
                else None
            ),
        }

    async def start_login(
        self, session: Session, *, row: ImConnection, actor_user_id: int
    ) -> dict[str, Any]:
        spec = self.registry.require_spec(row.platform)
        if not spec.supports_login:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "该平台不支持交互式登录", status_code=400
            )
        connector = self._login_connector(row)
        try:
            result = await connector.start_login()
        except ConnectorError as exc:
            raise _login_domain_error(exc) from exc
        # openilink 返回的 qrcode_img_content 是待编码内容（通常为 URL），不是图片 URL。
        # 统一在服务端生成 PNG base64，前端只负责展示，避免误当 URL 导致破图。
        result["qrcode_img_content"] = _qr_image_base64(
            result.get("qrcode_img_content") or result.get("qrcode") or ""
        )
        self.audit.add(
            session,
            action=AuditAction.CONNECTION_LOGIN_STARTED,
            resource_type="im_connection",
            resource_id=str(row.id),
            actor_user_id=actor_user_id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["login"]},
        )
        return result

    async def poll_login(
        self, session: Session, *, row: ImConnection, actor_user_id: int
    ) -> dict[str, Any]:
        connector = self._login_connectors.get(row.id)
        if connector is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "尚未发起扫码登录，请先获取二维码",
                status_code=400,
            )
        try:
            result = await connector.poll_login()
        except ConnectorError as exc:
            raise _login_domain_error(exc) from exc
        if result.get("status") != "confirmed":
            return {"status": str(result.get("status") or "wait")}

        token = str(result.get("bot_token") or "").strip()
        external_user_id = str(result.get("ilink_user_id") or "").strip()
        if not token or not external_user_id:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "扫码确认信息不完整，请重新扫码",
                status_code=400,
            )
        config = self.decrypt_config(row)
        config["token"] = token
        base_url = str(result.get("baseurl") or "").strip()
        if base_url:
            config["base_url"] = base_url
        problems = self.registry.validate_config(row.platform, config)
        if problems:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "; ".join(problems), status_code=400
            )
        row.config_ciphertext = self._encrypt_config(config)
        self.repo.bind_external_user(session, row.id, external_user_id)
        await run_in_threadpool(session.flush)
        await run_in_threadpool(session.refresh, row)
        self._login_connectors.pop(row.id, None)
        self.audit.add(
            session,
            action=AuditAction.CONNECTION_UPDATED,
            resource_type="im_connection",
            resource_id=str(row.id),
            actor_user_id=actor_user_id,
            owner_user_id=row.owner_user_id,
            metadata={"fields": ["login", "binding"]},
        )
        # Token 由服务端原子保存，不再返回浏览器。
        return {"status": "confirmed", "bound": True}

    def _login_connector(self, row: ImConnection) -> Connector:
        """登录流程的连接器实例按 connection_id 缓存。

        start_login 与 poll_login 是两次独立请求，必须共享同一实例，
        否则二维码状态（_pending_qrcode / SDK client）会丢失。
        连接被删除或重新配置时应清除缓存（见 delete/update 路径）。
        """
        connector = self._login_connectors.get(row.id)
        if connector is None:
            connector = self.registry.create(
                row.platform, _connector_context(row, self.decrypt_config(row))
            )
            self._login_connectors[row.id] = connector
        return connector

    # ------------------------------------------------------------------
    # 进站处理（连接器回调与 /connectors/* 入口共用）
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 连接器运行时状态持久化
    # ------------------------------------------------------------------

    def runtime_state_recorder(self):
        """返回连接管理器使用的状态补丁回调（独立短会话，不阻塞事件循环事务）。"""
        from ..core.db import SessionLocal

        repo = self.repo

        async def record(connection_id: int, patch: dict[str, Any]) -> None:
            with SessionLocal() as session:
                repo.apply_runtime_patch(session, connection_id, patch)
                session.commit()

        return record

    def inbound_handler(self):
        """返回连接器运行时使用的进站回调（独立短会话）。"""
        from ..core.db import SessionLocal

        service = self

        async def handle(connection_id: int, message: InboundMessage) -> str:
            with SessionLocal() as session:
                try:
                    row = service.repo.get(session, connection_id)
                    if row is None:
                        return InboundResult.UNHANDLED.value
                    result = service.handle_inbound(session, row=row, message=message)
                    session.commit()
                    if result is InboundResult.BOUND and not row.desired_running:
                        from ..connectors import connection_manager as manager

                        await manager.stop(connection_id)
                        row.state = ConnectionState.STOPPED
                        row.next_retry_at = None
                        session.commit()
                    return result.value
                except Exception:
                    session.rollback()
                    raise

        return handle

    def handle_inbound(
        self, session: Session, *, row: ImConnection, message: InboundMessage
    ) -> InboundResult:
        """统一进站处理：幂等 -> 绑定校验 -> 回复定位 -> 首个回复条件提交。"""
        if not message.external_message_id:
            return InboundResult.UNHANDLED
        sender_fingerprint = hashlib.sha256(
            f"{row.id}:{message.sender_external_id}".encode()
        ).hexdigest()[:32]
        payload_hash = hashlib.sha256(
            json.dumps(
                {
                    "sender": message.sender_external_id,
                    "text": message.text,
                    "reply_to": message.reply_to_public_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()

        # 1. 幂等裁决：connection_id + external_message_id 全局唯一。
        receipt = self.repo.record_receipt(
            session,
            connection_id=row.id,
            external_message_id=message.external_message_id,
            sender_fingerprint=sender_fingerprint,
            payload_hash=payload_hash,
            result_code=InboundResult.ACCEPTED.value,
        )
        if receipt is None:
            return InboundResult.DUPLICATE

        # 2. 绑定校验：未绑定时可凭一次性绑定码绑定；绑定后发送者必须匹配。
        if row.bound_external_user_id is None:
            binding_ok = False
            if message.binding_code and row.binding_code_hash:
                binding_ok = verify_binding_code(message.binding_code, row.binding_code_hash) and (
                    row.binding_code_expires_at is None or row.binding_code_expires_at > utc_now()
                )
            if not binding_ok:
                receipt.result_code = InboundResult.UNBOUND.value
                return InboundResult.UNBOUND
            self.repo.bind_external_user(session, row.id, message.sender_external_id)
            row.bound_external_user_id = message.sender_external_id
            self.audit.add(
                session,
                action=AuditAction.CONNECTION_BOUND,
                resource_type="im_connection",
                resource_id=str(row.id),
                actor_user_id=None,
                owner_user_id=row.owner_user_id,
                metadata={"fields": ["bound_external_user_id"]},
            )
            receipt.result_code = InboundResult.BOUND.value
            return InboundResult.BOUND
        if message.sender_external_id and message.sender_external_id != row.bound_external_user_id:
            receipt.result_code = InboundResult.UNBOUND.value
            return InboundResult.UNBOUND

        # 3. 任务回复定位与首个回复条件提交。
        result = self._submit_task_reply(session, row=row, message=message, receipt=receipt)
        receipt.result_code = result.value
        if result is InboundResult.ACCEPTED and receipt.task_id:
            task = session.get(RequestTask, receipt.task_id)
            if task is not None:
                self.audit.add(
                    session,
                    action=AuditAction.TASK_REPLY_SUBMITTED,
                    resource_type="request_task",
                    resource_id=str(task.id),
                    actor_user_id=None,
                    owner_user_id=task.owner_user_id,
                    metadata={"fields": ["response_payload"], "source": "im"},
                )
        return result

    def _submit_task_reply(
        self,
        session: Session,
        *,
        row: ImConnection,
        message: InboundMessage,
        receipt,
    ) -> InboundResult:
        """把进站文本提交为任务回复（首个有效提交获胜）。

        正文经 IM DSL 解析为 ReplyDraft（思考 / 假 tool call / 最终文本），与 Web
        编辑器共享同一结构且往返不丢字段；无围栏块时整段作为 final_text，向后兼容
        M4 纯文本回复（docs/API_CONTRACT.md §9、docs/PRODUCT.md §6.4）。
        定位语义：回复上下文 > `#<task_public_id> <正文>` > 唯一等待任务默认。
        """
        text = (message.text or "").strip()
        if not text:
            return InboundResult.UNHANDLED
        task: RequestTask | None = None
        if message.reply_to_public_id:
            task = self._find_task_by_public_id(session, row, message.reply_to_public_id)
            if task is None:
                return InboundResult.UNHANDLED
        elif text.startswith("#"):
            public_id, _, rest = text[1:].partition(" ")
            task = self._find_task_by_public_id(session, row, public_id)
            if task is not None:
                text = rest.strip()
        if task is None:
            waiting = self._sole_waiting_task(session, row)
            if waiting is not None:
                task = waiting
        if task is None or not text:
            return InboundResult.UNHANDLED

        draft = parse_reply(text)
        if is_empty_draft(draft):
            return InboundResult.UNHANDLED
        accepted = self.tasks.first_reply_wins(
            session,
            task_id=task.id,
            owner_user_id=task.owner_user_id,
            expected_version=task.version,
            response_payload_json=draft.model_dump_json(exclude_none=True),
        )
        if accepted:
            self._add_task_event(
                session,
                task_id=task.id,
                event_type=TaskEventType.REPLY_SUBMITTED,
                actor_type=ActorType.IM,
                actor_user_id=row.owner_user_id,
                payload={"source": "im", "connection_id": row.id},
            )
            receipt.task_id = task.id
            return InboundResult.ACCEPTED
        # 晚到回复：只记录事件与审计，不覆盖已接受响应。
        self._add_task_event(
            session,
            task_id=task.id,
            event_type=TaskEventType.REPLY_REJECTED_LATE,
            actor_type=ActorType.IM,
            actor_user_id=row.owner_user_id,
            payload={"source": "im", "connection_id": row.id, "payload_hash": receipt.payload_hash},
        )
        receipt.task_id = task.id
        return InboundResult.LATE

    def _find_task_by_public_id(
        self, session: Session, row: ImConnection, public_id: str
    ) -> RequestTask | None:
        from sqlalchemy import select

        if not public_id:
            return None
        return session.execute(
            select(RequestTask).where(
                RequestTask.public_id == public_id,
                RequestTask.owner_user_id == row.owner_user_id,
            )
        ).scalar_one_or_none()

    def _sole_waiting_task(self, session: Session, row: ImConnection) -> RequestTask | None:
        """仅存在唯一等待任务时返回它，作为默认回复定位。"""
        from sqlalchemy import select

        from ..domain.enums import TaskState

        rows = list(
            session.scalars(
                select(RequestTask).where(
                    RequestTask.owner_user_id == row.owner_user_id,
                    RequestTask.state == TaskState.WAITING_HUMAN,
                )
            )
        )
        return rows[0] if len(rows) == 1 else None

    @staticmethod
    def _add_task_event(
        session: Session,
        *,
        task_id: int,
        event_type: TaskEventType,
        actor_type: ActorType,
        actor_user_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        from ..repositories.models import TaskEvent

        session.add(
            TaskEvent(
                task_id=task_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_user_id=actor_user_id,
                payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
                request_id=get_request_id(),
            )
        )


def _connector_context(row: ImConnection, config: dict[str, Any]):
    from ..connectors.base import ConnectorContext

    return ConnectorContext(
        connection_id=row.id,
        owner_user_id=row.owner_user_id,
        name=row.name,
        platform=row.platform,
        config=config,
    )


def _login_domain_error(exc: ConnectorError) -> DomainError:
    """把连接器登录错误映射为对外领域错误（不泄露内部堆栈）。"""
    status = 401 if exc.is_auth else 400
    return DomainError(
        DomainErrorCode.VALIDATION_FAILED,
        exc.message or "扫码登录失败，请重试",
        status_code=status,
    )


def _qr_image_base64(content: Any) -> str:
    """把 SDK 的二维码内容规范为 PNG base64。"""
    if isinstance(content, (bytes, bytearray)):
        return base64.b64encode(bytes(content)).decode("ascii")
    value = str(content or "").strip()
    if not value:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED, "二维码内容为空，请重试", status_code=400
        )
    if value.startswith("data:image/") and ";base64," in value:
        return value.split(",", 1)[1]
    try:
        decoded = base64.b64decode(value, validate=True)
        if decoded.startswith(b"\x89PNG"):
            return value
    except (ValueError, TypeError):
        pass

    import qrcode

    output = io.BytesIO()
    qrcode.make(value).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def _run_coroutine(coro):
    """在同步 API 上下文中执行连接器协程。"""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return loop.run_until_complete(coro)
