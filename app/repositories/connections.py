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
        return session.get(ImConnection, connection_id)

    def get_owned(
        self, session: Session, connection_id: int, owner_user_id: int
    ) -> ImConnection | None:
        row = self.get(session, connection_id)
        if row is None or row.owner_user_id != owner_user_id:
            return None
        return row

    def get_by_owner_platform(
        self, session: Session, owner_user_id: int, platform: str
    ) -> ImConnection | None:
        return session.execute(
            select(ImConnection).where(
                ImConnection.owner_user_id == owner_user_id,
                ImConnection.platform == platform,
            )
        ).scalar_one_or_none()

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
        filters: list[Any] = []
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
                    ImConnection.desired_running.is_(True),
                )
            )
        )

    def count_api_key_references(
        self, session: Session, connection_id: int, *, enabled_only: bool = True
    ) -> tuple[int, list[str]]:
        """引用该连接的 API Key 前缀列表；enabled_only=False 时包含停用 Key。"""
        from .models import ApiKey

        filters = [ApiKey.im_connection_id == connection_id]
        if enabled_only:
            filters.append(ApiKey.is_enabled.is_(True))
        prefixes = list(
            session.scalars(
                select(ApiKey.key_prefix).where(*filters).order_by(ApiKey.id.asc()).limit(50)
            )
        )
        total = session.scalar(select(func.count()).select_from(ApiKey).where(*filters)) or 0
        return total, prefixes

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def add(self, session: Session, connection: ImConnection) -> ImConnection:
        session.add(connection)
        return connection

    def delete(self, session: Session, connection_id: int) -> None:
        row = session.get(ImConnection, connection_id)
        if row is not None:
            session.delete(row)

    def set_desired_running(self, session: Session, connection_id: int, desired: bool) -> None:
        session.execute(
            update(ImConnection)
            .where(ImConnection.id == connection_id)
            .values(desired_running=desired, updated_at=_now())
        )

    def disable_if_desired(
        self, session: Session, connection_id: int, *, expected_updated_at: datetime
    ) -> bool:
        """仅当连接自快照以来未被改动时原子地停用（乐观锁）。

        用于看门狗：在异步健康检查之后写回停用状态时做二次校验，避免用
        陈旧快照覆盖用户并发的 start 操作。条件用 ``updated_at == 快照``
        而非 ``desired_running is True``——因为用户并发 start 后 desired_running
        同样为 True，无法据此区分"看门狗要停用的 True"与"用户刚 start 的 True"；
        而 start 会更新 updated_at，故用 updated_at 快照可精确检测并发修改。
        返回是否实际执行了停用（即快照以来无并发修改）。
        """
        result = session.execute(
            update(ImConnection)
            .where(
                ImConnection.id == connection_id,
                ImConnection.updated_at == expected_updated_at,
            )
            .values(desired_running=False, updated_at=_now())
        )
        return result.rowcount == 1

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
        self, session: Session, connection_id: int, code_hash: str, expires_at: datetime | None
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
        try:
            # 先 flush 触发唯一约束，尽早暴露并发重复，避免在调用方事务提交时
            # 才抛出导致整笔事务失败。SAVEPOINT 保证仅回滚本次插入。
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:
            # 并发下另一事务已插入同 (connection_id, task_id) 记录：返回已存在行。
            session.expunge(row)
            return session.execute(
                select(ConnectorOutbox).where(
                    ConnectorOutbox.connection_id == connection_id,
                    ConnectorOutbox.task_id == task_id,
                )
            ).scalar_one()
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
    ) -> bool:
        """标记投递失败并重试。

        仅允许 PENDING 状态重试；已 ACKED/DELIVERED 的终态记录不会被回退为
        PENDING（避免已确认消息被重复投递）。返回是否实际更新。
        """
        result = session.execute(
            update(ConnectorOutbox)
            .where(
                ConnectorOutbox.connection_id == connection_id,
                ConnectorOutbox.task_id == task_id,
                ConnectorOutbox.delivery_state == OutboxDeliveryState.PENDING,
            )
            .values(
                delivery_state=OutboxDeliveryState.PENDING,
                attempt_count=ConnectorOutbox.attempt_count + 1,
                last_error_code=error_code[:64],
                updated_at=_now(),
            )
        )
        return result.rowcount == 1

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
