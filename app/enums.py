from enum import StrEnum


class ConnectorPlatform(StrEnum):
    WECOM = "wecom"
    WECHAT_ILINK = "wechat_ilink"
    WEBHOOK = "webhook"
    WEBSOCKET = "websocket"
    HTTP = "http"


class ConnectorStatus(StrEnum):
    OFFLINE = "offline"
    CONNECTING = "connecting"
    ONLINE = "online"
    ERROR = "error"
    STOPPED = "stopped"
    PENDING_RESTART = "pending_restart"


class BindingStatus(StrEnum):
    UNBOUND = "unbound"
    WAITING = "waiting"
    BOUND = "bound"
    EXPIRED = "expired"
    LOCKED = "locked"


class ErrorCode(StrEnum):
    AUTH_EXPIRED = "auth_expired"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    VALIDATION_FAILED = "validation_failed"
    CONFLICT = "conflict"
    BINDING_LOCKED = "binding_locked"
    BINDING_EXPIRED = "binding_expired"
    CONNECTOR_ERROR = "connector_error"
    LLM_ERROR = "llm_error"
    HUMAN_TIMEOUT = "human_timeout"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"


class ErrorAction(StrEnum):
    RELOGIN = "relogin"
    RESCAN = "rescan"
    REGENERATE_BINDING = "regenerate_binding"
    RETRY_START = "retry_start"
    APPLY_CONFIG = "apply_config"
    WAIT_AND_RETRY = "wait_and_retry"
    VIEW_LOGS = "view_logs"
    CONTACT_ADMIN = "contact_admin"
    FIX_INPUT = "fix_input"
    NONE = "none"


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
