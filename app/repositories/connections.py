"""IM 连接、outbox 与进站回执仓库。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.time import utc_now
from ..domain.enums import ConnectionState, OutboxDeliveryState
from .models import ConnectorOutbox, ImConnection, InboundReceipt


def _now() -> datetime:
    return utc_now()


class ConnectionRepository:
    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, session: Session, connection_id: int) -> ImConnection | None:
        row = session.get(ImConnection, connection_id)
        if row is None or row.deleted_at is not None:
            return None
        return row

    def get_owned(
        self, session: Session, connection_id: int, owner_user_id: int
    ) -> ImConnection | None:
        row = self.get(session, connection_id)
        if row is None or row.owner_user_id != owner_user_id:
            return None
        return row

    def list_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        owner_user_id: int | None = None,
        search: str | None = None,
        platform: str | None = None,
        state: ConnectionState | None = None,
    ) -> tuple[list[ImConnection], int]:
        filters: list[Any] = [ImConnection.deleted_at.is_(None)]
        if owner_user_id is not None:
            filters.append(ImConnection.owner_user_id == owner_user_id)
        if platform:
            filters.append(ImConnection.platform == platform)
        if state is not None:
            filters.append(ImConnection.state == state)
        if search:
            term = search.strip()
            filters.append(ImConnection.name.ilike(f"%{term}%"))
        total = session.scalar(select(func.count()).select_from(ImConnection).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(ImConnection)
                .where(*filters)
                .order_by(ImConnection.created_at.desc(), ImConnection.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def list_desired_running(self, session: Session) -> list[ImConnection]:
        return list(
            session.scalars(
                select(ImConnection).where(
                    ImConnection.deleted_at.is_(None),
                    ImConnection.desired_running.is_(True),
                )
            )
        )

    def count_enabled_api_key_references(self, session: Session, connection_id: int) -> int:
        from .models import ApiKey

        return (
            session.scalar(
                select(func.count())
                .select_from(ApiKey)
                .where(
                    ApiKey.im_connection_id == connection_id,
                    ApiKey.is_enabled.is_(True),
                    ApiKey.deleted_at.is_(None),
                )
            )
            or 0
        )

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def add(self, session: Session, connection: ImConnection) -> ImConnection:
        session.add(connection)
        return connection

    def soft_delete(self, session: Session, connection_id: int) -> None:
        session.execute(
            update(ImConnection)
            .where(ImConnection.id == connection_id, ImConnection.deleted_at.is_(None))
            .values(deleted_at=_now(), updated_at=_now())
        )

    def set_desired_running(self, session: Session, connection_id: int, desired: bool) -> None:
        session.execute(
            update(ImConnection)
            .where(ImConnection.id == connection_id)
            .values(desired_running=desired, updated_at=_now())
        )

    def apply_runtime_patch(
        self, session: Session, connection_id: int, patch: dict[str, Any]
    ) -> None:
        """连接管理器状态补丁：state/错误/退避字段（白名单列）。"""
        allowed = {
            "state",
            "last_error_code",
            "last_error_message",
            "retry_count",
            "next_retry_at",
            "last_authenticated_at",
            "last_health_at",
        }
        values = {key: value for key, value in patch.items() if key in allowed}
        if not values:
            return
        values.setdefault("updated_at", _now())
        session.execute(
            update(ImConnection).where(ImConnection.id == connection_id).values(**values)
        )

    def record_online(self, session: Session, connection_id: int) -> None:
        now = _now()
        session.execute(
            update(ImConnection)
            .where(ImConnection.id == connection_id)
            .values(
                state=ConnectionState.ONLINE,
                next_retry_at=None,
                last_authenticated_at=now,
                last_health_at=now,
                updated_at=now,
            )
        )

    def set_retry(self, session: Session, connection_id: int, retry_count: int) -> None:
        session.execute(
            update(ImConnection)
            .where(ImConnection.id == connection_id)
            .values(retry_count=retry_count, updated_at=_now())
        )

    # ------------------------------------------------------------------
    # 绑定
    # ------------------------------------------------------------------

    def set_binding_code(
        self, session: Session, connection_id: int, code_hash: str, expires_at: datetime
    ) -> None:
        session.execute(
            update(ImConnection)
            .where(ImConnection.id == connection_id)
            .values(
                binding_code_hash=code_hash,
                binding_code_expires_at=expires_at,
                updated_at=_now(),
            )
        )

    def bind_external_user(
        self, session: Session, connection_id: int, external_user_id: str
    ) -> None:
        session.execute(
            update(ImConnection)
            .where(ImConnection.id == connection_id)
            .values(
                bound_external_user_id=external_user_id,
                binding_code_hash=None,
                binding_code_expires_at=None,
                updated_at=_now(),
            )
        )

    # ------------------------------------------------------------------
    # Outbox（Webhook / WebSocket / HTTP 轮询可靠投递）
    # ------------------------------------------------------------------

    def enqueue_outbox(
        self,
        session: Session,
        *,
        connection_id: int,
        task_id: int,
        payload: dict[str, Any],
    ) -> ConnectorOutbox:
        existing = session.execute(
            select(ConnectorOutbox).where(
                ConnectorOutbox.connection_id == connection_id,
                ConnectorOutbox.task_id == task_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        row = ConnectorOutbox(
            connection_id=connection_id,
            task_id=task_id,
            payload_json=json.dumps(payload, ensure_ascii=False),
            delivery_state=OutboxDeliveryState.PENDING,
            available_at=_now(),
        )
        session.add(row)
        return row

    def mark_outbox_delivered(self, session: Session, connection_id: int, task_id: int) -> bool:
        result = session.execute(
            update(ConnectorOutbox)
            .where(
                ConnectorOutbox.connection_id == connection_id,
                ConnectorOutbox.task_id == task_id,
                ConnectorOutbox.delivery_state == OutboxDeliveryState.PENDING,
            )
            .values(delivery_state=OutboxDeliveryState.DELIVERED, updated_at=_now())
        )
        return result.rowcount == 1

    def mark_outbox_failed(
        self, session: Session, connection_id: int, task_id: int, error_code: str
    ) -> None:
        session.execute(
            update(ConnectorOutbox)
            .where(
                ConnectorOutbox.connection_id == connection_id,
                ConnectorOutbox.task_id == task_id,
            )
            .values(
                delivery_state=OutboxDeliveryState.PENDING,
                attempt_count=ConnectorOutbox.attempt_count + 1,
                last_error_code=error_code[:64],
                updated_at=_now(),
            )
        )

    def claim_outbox_batch(
        self,
        session: Session,
        *,
        connection_id: int,
        after_cursor: int,
        limit: int,
    ) -> list[ConnectorOutbox]:
        """按单调 cursor 拉取待投递任务包（幂等：只推进 delivery_state）。"""
        rows = list(
            session.scalars(
                select(ConnectorOutbox)
                .where(
                    ConnectorOutbox.connection_id == connection_id,
                    ConnectorOutbox.id > after_cursor,
                    ConnectorOutbox.delivery_state.in_(
                        [OutboxDeliveryState.PENDING, OutboxDeliveryState.DELIVERED]
                    ),
                )
                .order_by(ConnectorOutbox.id)
                .limit(limit)
            )
        )
        session.execute(
            update(ConnectorOutbox)
            .where(
                ConnectorOutbox.connection_id == connection_id,
                ConnectorOutbox.id > after_cursor,
                ConnectorOutbox.delivery_state == OutboxDeliveryState.PENDING,
                ConnectorOutbox.id.in_([row.id for row in rows]) if rows else False,
            )
            .values(delivery_state=OutboxDeliveryState.DELIVERED, updated_at=_now())
        )
        return rows

    def ack_outbox(self, session: Session, connection_id: int, task_id: int) -> bool:
        result = session.execute(
            update(ConnectorOutbox)
            .where(
                ConnectorOutbox.connection_id == connection_id,
                ConnectorOutbox.task_id == task_id,
                ConnectorOutbox.acked_at.is_(None),
            )
            .values(
                delivery_state=OutboxDeliveryState.ACKED,
                acked_at=_now(),
                updated_at=_now(),
            )
        )
        return result.rowcount == 1

    def count_pending_outbox(self, session: Session, connection_id: int) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(ConnectorOutbox)
                .where(
                    ConnectorOutbox.connection_id == connection_id,
                    ConnectorOutbox.delivery_state.in_(
                        [OutboxDeliveryState.PENDING, OutboxDeliveryState.DELIVERED]
                    ),
                )
            )
            or 0
        )

    # ------------------------------------------------------------------
    # 进站回执（connection_id + external_message_id 全局幂等）
    # ------------------------------------------------------------------

    def record_receipt(
        self,
        session: Session,
        *,
        connection_id: int,
        external_message_id: str,
        sender_fingerprint: str = "",
        task_id: int | None = None,
        payload_hash: str = "",
        result_code: str,
    ) -> InboundReceipt | None:
        """写入回执；重复消息 ID 唯一约束冲突时返回 None（幂等裁决）。"""
        row = InboundReceipt(
            connection_id=connection_id,
            external_message_id=external_message_id,
            sender_fingerprint=sender_fingerprint[:255],
            task_id=task_id,
            payload_hash=payload_hash[:64],
            result_code=result_code[:64],
        )
        existing = self.get_receipt(session, connection_id, external_message_id)
        if existing is not None:
            return None
        try:
            # 仅回滚回执插入（SAVEPOINT），不破坏调用方事务内的其他工作。
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:
            # 并发重复：只返回幂等裁决，不抛业务错误。
            session.expunge(row)
            return None
        return row

    def get_receipt(
        self, session: Session, connection_id: int, external_message_id: str
    ) -> InboundReceipt | None:
        return session.execute(
            select(InboundReceipt).where(
                InboundReceipt.connection_id == connection_id,
                InboundReceipt.external_message_id == external_message_id,
            )
        ).scalar_one_or_none()
