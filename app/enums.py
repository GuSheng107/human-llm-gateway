from enum import StrEnum


class ConnectorPlatform(StrEnum):
    WECOM = "wecom"
    WECHAT_ILINK = "wechat_ilink"
    WEBHOOK = "webhook"
    WEBSOCKET = "websocket"
    HTTP = "http"


class ConnectorStatus(StrEnum):
    DISABLED = "disabled"
    OFFLINE = "offline"
    CONNECTING = "connecting"
    ONLINE = "online"
    ERROR = "error"


class BindingStatus(StrEnum):
    UNBOUND = "unbound"
    BINDING = "binding"
    BOUND = "bound"
    EXPIRED = "expired"


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class RouteMode(StrEnum):
    HUMAN = "human"
    LLM = "llm"
    HUMAN_FALLBACK_LLM = "human_fallback_llm"


class TaskStatus(StrEnum):
    RECEIVED = "received"
    AUTHENTICATED = "authenticated"
    ROUTED = "routed"
    HUMAN_WAITING = "human_waiting"
    LLM_STREAMING = "llm_streaming"
    PSEUDO_STREAMING = "pseudo_streaming"
    TOOL_PENDING = "tool_pending"
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    FAILED = "failed"


class EventKind(StrEnum):
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    FINAL = "final"


class ReplySource(StrEnum):
    IM = "im"
    WEB = "web"
    LLM = "llm"
