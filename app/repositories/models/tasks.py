"""请求任务、事件与草稿：request_tasks / task_events / task_drafts。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db import Base
from ...domain.enums import (
    ActorType,
    DeliveryMode,
    DraftSource,
    DraftState,
    InferenceProtocol,
    ReplyStrategy,
    TaskEventType,
    TaskState,
)
from .base import TimestampMixin, VersionMixin, sa_enum, utc_now


class RequestTask(TimestampMixin, VersionMixin, Base):
    __tablename__ = "request_tasks"
    __table_args__ = (
        CheckConstraint(
            "(protocol = 'openai_responses' AND response_public_id IS NOT NULL) OR "
            "(protocol <> 'openai_responses' AND response_public_id IS NULL)",
            name="ck_request_tasks_response_id",
        ),
        Index("ix_request_tasks_owner_state_created", "owner_user_id", "state", "created_at"),
        Index("ix_request_tasks_api_key_created", "api_key_id", "created_at"),
        Index("ix_request_tasks_model_created", "requested_model", "created_at"),
        Index("ix_request_tasks_previous", "previous_task_id"),
        Index("ix_request_tasks_origin_trace", "origin_trace_id"),
        Index("ix_request_tasks_deadline", "state", "human_deadline_at"),
        Index("ix_request_tasks_slot_released", "slot_released_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    response_public_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    previous_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("request_tasks.id"), nullable=True
    )
    origin_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    api_key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id", ondelete="RESTRICT"), nullable=False
    )
    api_key_prefix_snapshot: Mapped[str] = mapped_column(String(8), nullable=False)
    fake_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("fake_models.id", ondelete="SET NULL"), nullable=True
    )
    requested_model: Mapped[str] = mapped_column(String(255), nullable=False)
    protocol: Mapped[InferenceProtocol] = mapped_column(sa_enum(InferenceProtocol), nullable=False)
    raw_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_request_json: Mapped[str] = mapped_column(Text, nullable=False)
    request_headers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    stream_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reply_strategy_snapshot: Mapped[ReplyStrategy] = mapped_column(
        sa_enum(ReplyStrategy), nullable=False
    )
    delivery_mode_snapshot: Mapped[DeliveryMode] = mapped_column(
        sa_enum(DeliveryMode), nullable=False
    )
    im_connection_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_config_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_config_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    state: Mapped[TaskState] = mapped_column(sa_enum(TaskState), nullable=False)
    response_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    slot_acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    slot_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    response_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        Index("ix_task_events_task_id", "task_id", "id"),
        Index("ix_task_events_type_created", "event_type", "created_at"),
        Index("ix_task_events_request", "request_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("request_tasks.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[TaskEventType] = mapped_column(sa_enum(TaskEventType), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(sa_enum(ActorType), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class TaskDraft(TimestampMixin, VersionMixin, Base):
    __tablename__ = "task_drafts"
    __table_args__ = (
        Index("ix_task_drafts_task_state_updated", "task_id", "state", "updated_at"),
        Index("ix_task_drafts_owner_updated", "owner_user_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("request_tasks.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    source: Mapped[DraftSource] = mapped_column(sa_enum(DraftSource), nullable=False)
    source_llm_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[DraftState] = mapped_column(
        sa_enum(DraftState), default=DraftState.EDITING, nullable=False
    )
    reasoning_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    final_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskInboxState(TimestampMixin, Base):
    """工作台收件箱未读状态（M14）：task_id + owner 唯一，首次 seen 创建。

    last_seen_event_id 记录用户在该任务上确认到的最新事件 ID，
    用于"有更新"徽标。seen 写入走独立短事务，不与主任务事务并发。
    """

    __tablename__ = "task_inbox_states"
    __table_args__ = (Index("ix_task_inbox_states_owner", "owner_user_id", "seen_at"),)

    task_id: Mapped[int] = mapped_column(
        ForeignKey("request_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_events.id", ondelete="SET NULL"), nullable=True
    )
