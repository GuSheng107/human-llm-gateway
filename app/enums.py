from enum import StrEnum


class ConnectorPlatform(StrEnum):
    FAKE = "fake"
    TELEGRAM = "telegram"
    WECOM = "wecom"
    WECHAT_SIDECAR = "wechat_sidecar"


class ConnectorStatus(StrEnum):
    DISABLED = "disabled"
    OFFLINE = "offline"
    ONLINE = "online"
    ERROR = "error"


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
