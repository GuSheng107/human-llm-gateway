from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .enums import (
    BindingStatus,
    ConnectorPlatform,
    ConnectorStatus,
    EventKind,
    ReplySource,
    RouteMode,
    TaskStatus,
    UserRole,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.USER, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    im_connections: Mapped[list["IMConnection"]] = relationship(back_populates="owner")


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
    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[ConnectorPlatform] = mapped_column(Enum(ConnectorPlatform), index=True)
    config_json: Mapped[str] = mapped_column(Text)
    status: Mapped[ConnectorStatus] = mapped_column(Enum(ConnectorStatus), default=ConnectorStatus.OFFLINE)
    binding_status: Mapped[BindingStatus] = mapped_column(
        Enum(BindingStatus), default=BindingStatus.UNBOUND, index=True
    )
    bound_user_id: Mapped[str] = mapped_column(String(200), default="")
    bound_conversation_id: Mapped[str] = mapped_column(String(200), default="")
    binding_code_hash: Mapped[str] = mapped_column(String(255), default="")
    binding_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner: Mapped["AdminUser"] = relationship(back_populates="im_connections")
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


class InboundReceipt(Base):
    """全局进站幂等凭据，避免同一平台消息被两个等待任务消费。"""

    __tablename__ = "inbound_receipts"
    __table_args__ = (UniqueConstraint("connector_id", "external_message_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("im_connections.id"), index=True)
    external_message_id: Mapped[str] = mapped_column(String(200))
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("request_tasks.id"), nullable=True, index=True
    )
    sender_id: Mapped[str] = mapped_column(String(200), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PublicModel(Base):
    """对外公开目录中的模型条目（GET /v1/models 的唯一数据来源）。

    与 LLMModel（上游供应商同步回来的模型目录）语义不同：PublicModel 由管理员
    直接维护，决定兼容 API 客户端能看到哪些模型 ID。全新数据库初始化时由
    app.model_catalog 中的默认常量做一次幂等种子，之后完全以数据库为准。
    """

    __tablename__ = "public_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    owned_by: Mapped[str] = mapped_column(String(120), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


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


class AppLog(Base):
    """运行时日志：连接器异常、请求处理错误、外部调用失败等。

    与 AuditLog 区别：AuditLog 记录管理员/系统的业务操作审计（创建、删除、改密等）；
    AppLog 记录运行期的技术性事件（启动失败、回调异常、LLM 超时等），用于排查问题。
    """

    __tablename__ = "app_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16), index=True, default="info")
    logger: Mapped[str] = mapped_column(String(80), index=True, default="app")
    message: Mapped[str] = mapped_column(Text)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
