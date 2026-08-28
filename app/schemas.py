from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    BindingStatus,
    ConnectorPlatform,
    ConnectorStatus,
    RouteMode,
    TaskStatus,
    UserRole,
)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    display_name: str
    role: UserRole


class CurrentUserSummary(BaseModel):
    id: int
    username: str
    display_name: str
    role: UserRole


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    role: UserRole = UserRole.USER


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    operator_name: str = Field(min_length=1, max_length=120)
    route_name: str = "human-default"
    model_name: str = "human-default"
    route_mode: RouteMode = RouteMode.HUMAN
    human_timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    provider_id: int | None = None
    human_operator_id: int | None = None
    im_connection_id: int
    route_id: int | None = None


class ApiKeySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    prefix: str
    active: bool
    operator_name: str
    im_name: str
    platform: ConnectorPlatform
    route_mode: RouteMode
    model_name: str


class ApiKeyCreated(ApiKeySummary):
    secret: str


class HumanReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    sender_id: str | None = Field(default=None, max_length=160)
    conversation_id: str | None = Field(default=None, max_length=160)
    external_message_id: str | None = Field(default=None, max_length=160)
    reply_to_task_id: str | None = Field(default=None, max_length=36)


class TaskSummary(BaseModel):
    id: str
    api_key_id: int
    protocol: str
    model: str
    status: TaskStatus
    error: str | None = None
    created_at: str


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    protocol: str = "openai_compatible"
    api_key: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class RouteCreate(BaseModel):
    name: str
    model_name: str
    upstream_model: str = ""
    mode: RouteMode
    model_names: list[str] = Field(default_factory=list)
    provider_id: int | None = None
    human_timeout_seconds: int = Field(default=300, ge=1, le=86_400)


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: ConnectorPlatform
    config: dict[str, Any] = Field(default_factory=dict)


class ProviderSummary(BaseModel):
    id: int
    name: str
    protocol: str
    base_url: str
    active: bool


class RouteSummary(BaseModel):
    id: int
    name: str
    model_name: str
    upstream_model: str
    model_names: list[str] = Field(default_factory=list)
    mode: RouteMode
    provider_id: int | None
    human_timeout_seconds: int


class PublicModelCreate(BaseModel):
    model_id: str = Field(min_length=1, max_length=200, pattern=r"^\S+$")
    owned_by: str = Field(default="", max_length=120)
    sort_order: int = Field(default=0, ge=-1_000_000, le=1_000_000)
    active: bool = True


class PublicModelUpdate(BaseModel):
    model_id: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^\S+$")
    owned_by: str | None = Field(default=None, max_length=120)
    sort_order: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    active: bool | None = None


class PublicModelSummary(BaseModel):
    id: int
    model_id: str
    owned_by: str
    sort_order: int
    active: bool


class ConnectionSummary(BaseModel):
    id: int
    name: str
    platform: ConnectorPlatform
    status: ConnectorStatus
    binding_status: BindingStatus = BindingStatus.UNBOUND
    owner_id: int
    owner_name: str
    bound_user_id: str = ""
    bound_conversation_id: str = ""
    last_seen_at: str | None = None
    last_error: str = ""
    created_at: str = ""


class BindingStartResponse(BaseModel):
    connection_id: int
    code: str
    command: str
    expires_at: str


class ConnectionCreated(ConnectionSummary):
    setup: dict[str, Any] = Field(default_factory=dict)
