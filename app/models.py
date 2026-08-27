from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .enums import ConnectorPlatform, ConnectorStatus, EventKind, ReplySource, RouteMode, TaskStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HumanOperator(Base):
    __tablename__ = "human_operators"
    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="offline")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    api_key: Mapped["ApiKey | None"] = relationship(back_populates="human_operator")


class IMConnection(Base):
    __tablename__ = "im_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[ConnectorPlatform] = mapped_column(Enum(ConnectorPlatform), index=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[ConnectorStatus] = mapped_column(Enum(ConnectorStatus), default=ConnectorStatus.OFFLINE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    api_key: Mapped["ApiKey | None"] = relationship(back_populates="im_connection")


class LLMProvider(Base):
    __tablename__ = "llm_providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    protocol: Mapped[str] = mapped_column(String(32), default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(500))
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    options_json: Mapped[str] = mapped_column(Text, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    routes: Mapped[list["ModelRoute"]] = relationship(back_populates="provider")
    models: Mapped[list["LLMModel"]] = relationship(back_populates="provider",
                                                     cascade="all, delete-orphan")


class LLMModel(Base):
    __tablename__ = "llm_models"
    __table_args__ = (UniqueConstraint("provider_id", "model_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("llm_providers.id"), index=True)
    model_id: Mapped[str] = mapped_column(String(200))
    owned_by: Mapped[str] = mapped_column(String(120), default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    provider: Mapped[LLMProvider] = relationship(back_populates="models")


class ModelRoute(Base):
    __tablename__ = "model_routes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    model_name: Mapped[str] = mapped_column(String(160))
    upstream_model: Mapped[str] = mapped_column(String(200), default="")
    allowed_models_json: Mapped[str] = mapped_column(Text, default="[]")
    mode: Mapped[RouteMode] = mapped_column(Enum(RouteMode), index=True)
    human_timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("llm_providers.id"), nullable=True)
    provider: Mapped["LLMProvider | None"] = relationship(back_populates="routes")
    api_key: Mapped["ApiKey | None"] = relationship(back_populates="route")


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("human_operator_id"), UniqueConstraint("im_connection_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(32), index=True)
    secret_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    human_operator_id: Mapped[int] = mapped_column(ForeignKey("human_operators.id"), unique=True)
    im_connection_id: Mapped[int] = mapped_column(ForeignKey("im_connections.id"), unique=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("model_routes.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    human_operator: Mapped[HumanOperator] = relationship(back_populates="api_key")
    im_connection: Mapped[IMConnection] = relationship(back_populates="api_key")
    route: Mapped[ModelRoute] = relationship(back_populates="api_key")
    tasks: Mapped[list["RequestTask"]] = relationship(back_populates="api_key")


class RequestTask(Base):
    __tablename__ = "request_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"), index=True)
    protocol: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(160))
    request_json: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    api_key: Mapped[ApiKey] = relationship(back_populates="tasks")
    events: Mapped[list["RequestEvent"]] = relationship(back_populates="task", order_by="RequestEvent.sequence")


class RequestEvent(Base):
    __tablename__ = "request_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("request_tasks.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[EventKind] = mapped_column(Enum(EventKind))
    content: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_args_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[ReplySource] = mapped_column(Enum(ReplySource))
    external_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    task: Mapped[RequestTask] = relationship(back_populates="events")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    subject_type: Mapped[str] = mapped_column(String(80))
    subject_id: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(120), default="system")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
