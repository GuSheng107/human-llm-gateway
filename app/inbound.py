from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .connectors.base import InboundMessage
from .connectors.manager import ConnectorManager
from .dblog import log_event
from .enums import BindingStatus, ReplySource, TaskStatus
from .im_connections import BIND_COMMAND, try_complete_binding, utc_now
from .models import ApiKey, AuditLog, IMConnection, InboundReceipt, RequestTask
from .services import TaskError, TaskService

TASK_LINE = re.compile(
    r"^\s*/task\s+([0-9a-fA-F-]{36})\s*(?:\r?\n)?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class InboundResult:
    accepted: bool
    reason: str
    task_id: str | None = None


class InboundProcessor:
    """统一处理所有 IM 进站消息：幂等、绑定鉴权与精确任务路由。"""

    def __init__(self, settings: Settings, manager: ConnectorManager) -> None:
        self.settings = settings
        self.manager = manager

    async def handle(self, db: Session, message: InboundMessage) -> InboundResult:
        connection = db.get(IMConnection, message.connector_id)
        if connection is None or connection.deleted_at is not None:
            return InboundResult(False, "connection_not_found")
        receipt = self._claim_receipt(db, message)
        if message.external_message_id and receipt is None:
            return InboundResult(False, "duplicate")
        if not message.sender_id:
            self._audit(db, connection, "connector.inbound_rejected", {"reason": "missing_sender"})
            return InboundResult(False, "missing_sender")

        if connection.binding_status is BindingStatus.BINDING and BIND_COMMAND.fullmatch(
            message.text
        ):
            if try_complete_binding(
                db,
                connection,
                text=message.text,
                sender_id=message.sender_id,
                conversation_id=message.conversation_id,
            ):
                await self.manager.send_notice(
                    connection.id,
                    message.conversation_id or message.sender_id,
                    "绑定成功。此后该 Bot 收到的任务只会接受你的回复。",
                )
                return InboundResult(True, "bound")
            await self.manager.send_notice(
                connection.id,
                message.conversation_id or message.sender_id,
                "绑定码无效或已过期，请回到网页重新开始绑定。",
            )
            self._audit(
                db,
                connection,
                "connector.binding_rejected",
                {"sender_id": message.sender_id},
            )
            return InboundResult(False, "invalid_binding_code")

        if not self._sender_allowed(connection, message.sender_id):
            self._audit(
                db,
                connection,
                "connector.inbound_rejected",
                {"reason": "sender_not_bound", "sender_id": message.sender_id},
            )
            return InboundResult(False, "sender_not_bound")

        task_reference, reply_text = self._task_reference(message)
        waiting = self._waiting_tasks(db, connection.id, task_reference)
        if not waiting:
            self._audit(db, connection, "connector.inbound_ignored", {"reason": "no_task"})
            return InboundResult(False, "no_waiting_task")
        if len(waiting) > 1:
            self._audit(
                db,
                connection,
                "connector.inbound_ignored",
                {"reason": "ambiguous_task", "count": len(waiting)},
            )
            await self.manager.send_notice(
                connection.id,
                message.conversation_id or message.sender_id,
                "当前有多个待回复任务，请在回复第一行添加 /task <任务ID>。",
            )
            return InboundResult(False, "ambiguous_task")

        task = waiting[0]
        if receipt is not None:
            receipt.task_id = task.id
        connection.last_seen_at = utc_now()
        try:
            TaskService(self.settings, self.manager).accept_reply(
                db,
                task.id,
                reply_text,
                ReplySource.IM,
                f"connector:{connection.id}:{message.sender_id}",
                message.external_message_id or None,
            )
        except TaskError as exc:
            db.commit()
            log_event(
                "warning",
                "connector.inbound",
                f"IM 回复未被任务接受: {exc}",
                {"connector_id": connection.id, "task_id": task.id},
            )
            return InboundResult(False, "task_rejected", task.id)
        return InboundResult(True, "reply_accepted", task.id)

    @staticmethod
    def _claim_receipt(db: Session, message: InboundMessage) -> InboundReceipt | None:
        if not message.external_message_id:
            return None
        receipt = InboundReceipt(
            connector_id=message.connector_id,
            external_message_id=message.external_message_id,
            sender_id=message.sender_id,
        )
        db.add(receipt)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return None
        return receipt

    @staticmethod
    def _sender_allowed(connection: IMConnection, sender_id: str) -> bool:
        return (
            connection.binding_status is BindingStatus.BOUND
            and bool(sender_id)
            and sender_id == connection.bound_user_id
        )

    @staticmethod
    def _task_reference(message: InboundMessage) -> tuple[str | None, str]:
        if message.reply_to_task_id:
            return message.reply_to_task_id, message.text
        match = TASK_LINE.match(message.text)
        if match is None:
            return None, message.text
        return match.group(1), message.text[match.end() :].lstrip()

    @staticmethod
    def _waiting_tasks(
        db: Session,
        connection_id: int,
        task_reference: str | None,
    ) -> list[RequestTask]:
        statement = (
            select(RequestTask)
            .join(ApiKey)
            .where(
                ApiKey.im_connection_id == connection_id,
                RequestTask.status.in_(
                    [TaskStatus.HUMAN_WAITING, TaskStatus.TOOL_PENDING]
                ),
            )
            .order_by(RequestTask.created_at.desc())
        )
        if task_reference:
            statement = statement.where(RequestTask.id == task_reference)
        else:
            statement = statement.limit(2)
        return list(db.execute(statement).scalars())

    @staticmethod
    def _audit(
        db: Session,
        connection: IMConnection,
        action: str,
        detail: dict[str, Any],
    ) -> None:
        db.add(
            AuditLog(
                action=action,
                subject_type="im_connection",
                subject_id=str(connection.id),
                actor="connector",
                detail_json=json.dumps(detail, ensure_ascii=False),
            )
        )
        db.commit()
