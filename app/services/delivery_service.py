"""任务投递用例：把任务包投递到 IM 连接。

IM 投递失败不影响 Web 任务可见性（docs/ROADMAP.md M4）；本服务把
投递结果写入 outbox 与任务事件，绝不向准入调用方抛出异常。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..connectors.base import Connector, DeliveryEnvelope
from ..connectors.manager import ConnectionManager
from ..core.time import iso_utc
from ..domain.connections import ERROR_CONFIG
from ..domain.enums import ActorType, TaskEventType
from ..repositories.connections import ConnectionRepository
from ..repositories.models import ImConnection, RequestTask

# 使用 outbox 可靠投递的平台（docs/DATABASE.md §4.2）
OUTBOX_PLATFORMS = frozenset({"webhook", "http_poll", "websocket"})


@dataclass
class DeliveryOutcome:
    connection_id: int
    platform: str
    delivered: bool
    error_code: str | None = None
    via_outbox: bool = False


class DeliveryService:
    def __init__(self, manager: ConnectionManager | None = None) -> None:
        self.repo = ConnectionRepository()
        self.manager = manager

    def _manager(self) -> ConnectionManager:
        from ..connectors import connection_manager as default_manager

        return self.manager or default_manager

    def deliver_task(
        self, session: Session, *, task: RequestTask, connection: ImConnection
    ) -> DeliveryOutcome:
        """投递任务包到指定连接；任何失败只记录，不抛出。"""
        envelope = self.build_envelope(task)
        payload = envelope.to_json()
        via_outbox = connection.platform in OUTBOX_PLATFORMS
        if via_outbox:
            self.repo.enqueue_outbox(
                session, connection_id=connection.id, task_id=task.id, payload=payload
            )
        connector = self._manager().get_instance(connection.id)
        if connector is None:
            return DeliveryOutcome(
                connection_id=connection.id,
                platform=connection.platform,
                delivered=False,
                error_code="connection_offline",
                via_outbox=via_outbox,
            )
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            # 事件循环上下文（异步请求）：投递转为后台任务，任务包留在
            # outbox，由异步路径确认；同步路径才允许内联推送。
            import asyncio as _asyncio

            _asyncio.get_running_loop().create_task(
                self._async_push(connector, envelope, connection.id, task.id, via_outbox)
            )
            return DeliveryOutcome(
                connection_id=connection.id,
                platform=connection.platform,
                delivered=False,
                error_code="delivery_scheduled",
                via_outbox=via_outbox,
            )
        try:
            _run(connector.deliver(envelope))
        except Exception as exc:  # noqa: BLE001  # 投递失败不影响任务
            error_code = getattr(exc, "code", ERROR_CONFIG)
            if via_outbox:
                self.repo.mark_outbox_failed(session, connection.id, task.id, str(error_code))
            self._add_event(session, task, connection, delivered=False, error_code=str(error_code))
            return DeliveryOutcome(
                connection_id=connection.id,
                platform=connection.platform,
                delivered=False,
                error_code=str(error_code),
                via_outbox=via_outbox,
            )
        if via_outbox:
            self.repo.mark_outbox_delivered(session, connection.id, task.id)
        self._add_event(session, task, connection, delivered=True)
        return DeliveryOutcome(
            connection_id=connection.id,
            platform=connection.platform,
            delivered=True,
            via_outbox=via_outbox,
        )

    async def _async_push(
        self,
        connector: Connector,
        envelope: DeliveryEnvelope,
        connection_id: int,
        task_id: int,
        via_outbox: bool,
    ) -> None:
        """异步上下文的后台投递：使用独立短会话更新 outbox 与事件。"""
        from ..core.db import SessionLocal
        from ..repositories.models import RequestTask as TaskRow

        delivered = False
        error_code: str | None = None
        try:
            await connector.deliver(envelope)
            delivered = True
        except Exception as exc:  # noqa: BLE001
            error_code = str(getattr(exc, "code", ERROR_CONFIG))
        with SessionLocal() as session:
            task = session.get(TaskRow, task_id)
            connection = session.get(ImConnection, connection_id)
            if task is None or connection is None:
                return
            if via_outbox:
                if delivered:
                    self.repo.mark_outbox_delivered(session, connection_id, task_id)
                elif error_code:
                    self.repo.mark_outbox_failed(session, connection_id, task_id, error_code)
            self._add_event(session, task, connection, delivered=delivered, error_code=error_code)
            session.commit()

    # ------------------------------------------------------------------

    def build_envelope(self, task: RequestTask) -> DeliveryEnvelope:
        prompt, tool_names = self._extract_request_summary(task)
        return DeliveryEnvelope(
            task_public_id=task.public_id,
            requested_model=task.requested_model,
            prompt_text=prompt,
            owner_user_id=task.owner_user_id,
            created_at=iso_utc(task.created_at) or "",
            has_tools=bool(tool_names),
            tool_names=tool_names,
        )

    @staticmethod
    def _extract_request_summary(task: RequestTask) -> tuple[str, list[str]]:
        """从规范化请求提取展示用文本与工具名（防御式，M6 定义正式结构）。"""
        prompt = ""
        tool_names: list[str] = []
        try:
            normalized: dict[str, Any] = json.loads(task.normalized_request_json or "{}")
        except (ValueError, TypeError):
            normalized = {}
        messages = normalized.get("messages")
        if isinstance(messages, list) and messages:
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "user":
                    content = message.get("content")
                    if isinstance(content, str):
                        prompt = content
                    elif isinstance(content, list):
                        parts = [
                            block.get("text", "")
                            for block in content
                            if isinstance(block, dict) and block.get("type") == "text"
                        ]
                        prompt = "\n".join(part for part in parts if part)
                    break
        if not prompt:
            prompt = normalized.get("input") if isinstance(normalized.get("input"), str) else ""
        tools = normalized.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict):
                    name = tool.get("name") or (tool.get("function", {}) or {}).get("name")
                    if isinstance(name, str):
                        tool_names.append(name)
        return prompt[:4000], tool_names

    @staticmethod
    def _add_event(
        session: Session,
        task: RequestTask,
        connection: ImConnection,
        *,
        delivered: bool,
        error_code: str | None = None,
    ) -> None:
        from ..repositories.models import TaskEvent

        payload: dict[str, Any] = {
            "connection_id": connection.id,
            "platform": connection.platform,
            "delivered": delivered,
        }
        if error_code:
            payload["error_code"] = error_code
        session.add(
            TaskEvent(
                task_id=task.id,
                event_type=TaskEventType.DELIVERED,
                actor_type=ActorType.SYSTEM,
                payload_json=json.dumps(payload, ensure_ascii=False),
            )
        )


def _run(coro) -> None:
    import asyncio

    try:
        asyncio.get_running_loop().create_task(coro)  # pragma: no cover - 事件循环内由管理器处理
        return
    except RuntimeError:
        pass
    asyncio.run(coro)
