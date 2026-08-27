from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import ConnectorPlatform, ConnectorStatus, RouteMode, TaskStatus


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    operator_name: str = Field(min_length=1, max_length=120)
    im_name: str = Field(min_length=1, max_length=120)
    platform: ConnectorPlatform = ConnectorPlatform.FAKE
    im_config: dict[str, Any] = Field(default_factory=dict)
    route_name: str = "human-default"
    model_name: str = "human-default"
    route_mode: RouteMode = RouteMode.HUMAN
    human_timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    provider_id: int | None = None
    human_operator_id: int | None = None
    im_connection_id: int | None = None
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
    name: str
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


class ConnectionSummary(BaseModel):
    id: int
    name: str
    platform: ConnectorPlatform
    status: ConnectorStatus
