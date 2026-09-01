"""IM 连接与投递：im_connections / connector_outbox / inbound_receipts。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db import Base
from ...domain.enums import ConnectionState, OutboxDeliveryState
from .base import TimestampMixin, sa_enum, utc_now


class ImConnection(TimestampMixin, Base):
    __tablename__ = "im_connections"
    __table_args__ = (
        Index(
            "uq_im_connections_owner_platform",
            "owner_user_id",
            "platform",
            unique=True,
        ),
        Index("ix_im_connections_platform_state", "platform", "state"),
        Index("ix_im_connections_owner_state", "owner_user_id", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    config_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    config_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    desired_running: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    state: Mapped[ConnectionState] = mapped_column(
        sa_enum(ConnectionState), default=ConnectionState.STOPPED, nullable=False
    )
    bound_external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    binding_code_hash: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    binding_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConnectorOutbox(TimestampMixin, Base):
    __tablename__ = "connector_outbox"
    __table_args__ = (
        UniqueConstraint("connection_id", "task_id", name="uq_outbox_connection_task"),
        Index("ix_outbox_connection_id", "connection_id", "id"),
        Index("ix_outbox_delivery_available", "delivery_state", "available_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("im_connections.id"), nullable=False)
    task_id: Mapped[int] = mapped_column(ForeignKey("request_tasks.id"), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_state: Mapped[OutboxDeliveryState] = mapped_column(
        sa_enum(OutboxDeliveryState), default=OutboxDeliveryState.PENDING, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class InboundReceipt(Base):
    __tablename__ = "inbound_receipts"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "external_message_id", name="uq_inbound_connection_message"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("im_connections.id"), nullable=False)
    external_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_fingerprint: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("request_tasks.id"), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    result_code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
