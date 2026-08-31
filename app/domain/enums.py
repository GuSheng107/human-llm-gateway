"""领域枚举（稳定小写字符串值，见 docs/DATABASE.md §2.3）。"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class Capability(StrEnum):
    """管理台会话能力；前端权限只依赖这些稳定值。"""

    ACCOUNT_PASSWORD_CHANGE = "account.password.change"
    ACCOUNT_PROFILE_UPDATE = "account.profile.update"
    INVITATION_MANAGE = "invitation.manage"
    USER_MANAGE = "user.manage"
    CONNECTION_MANAGE = "connection.manage"
    MODEL_MANAGE = "model.manage"
    API_KEY_MANAGE = "api_key.manage"
    LOGS_MANAGE = "logs.manage"


class ConnectionState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    ONLINE = "online"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"


class LLMProtocol(StrEnum):
    """LLM 配置的上游协议编码（转发编码层，不等于客户端协议）。

    openai_chat      -> {base_url}/chat/completions
    openai_responses -> {base_url}/responses（支持 reasoning.effort）
    anthropic        -> {base_url}/v1/messages
    客户端协议（chat/responses/messages）与转发协议任意组合（cross 矩阵）。
    """

    OPENAI_CHAT = "openai_chat"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"


class ThinkingMode(StrEnum):
    MODEL_DEFAULT = "model_default"
    ENABLED = "enabled"
    DISABLED = "disabled"


class ThinkingLevel(StrEnum):
    """思考等级：OpenAI 两种格式映射 reasoning effort；Anthropic 映射思考预算。

    effort 档位对齐业内聚合器（newapi 等）通用的六档：
    minimal / low / medium / high / xhigh / max。
    """

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


# Anthropic 思考预算（tokens）：等级 -> budget_tokens（须 >= max_tokens 才生效）。
ANTHROPIC_THINKING_BUDGETS: dict[ThinkingLevel, int] = {
    ThinkingLevel.MINIMAL: 2048,
    ThinkingLevel.LOW: 4096,
    ThinkingLevel.MEDIUM: 8192,
    ThinkingLevel.HIGH: 16384,
    ThinkingLevel.XHIGH: 32768,
    ThinkingLevel.MAX: 65536,
}


class FakeModelScope(StrEnum):
    SYSTEM = "system"
    PRIVATE = "private"


class BillingTier(StrEnum):
    PAY_AS_YOU_GO = "pay_as_you_go"
    SUBSCRIPTION = "subscription"
    FREE = "free"
    DYNAMIC = "dynamic"


class ModelEndpointType(StrEnum):
    OPENAI_CHAT = "openai_chat"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"


class DeliveryMode(StrEnum):
    WEB = "web"
    IM = "im"


class ReplyStrategy(StrEnum):
    HUMAN = "human"
    LLM = "llm"
    HUMAN_FALLBACK_LLM = "human_fallback_llm"


class InferenceProtocol(StrEnum):
    OPENAI_CHAT = "openai_chat"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"


class TaskState(StrEnum):
    RECEIVED = "received"
    WAITING_HUMAN = "waiting_human"
    FORWARDING_LLM = "forwarding_llm"
    RESPONSE_READY = "response_ready"
    RESPONDING = "responding"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class DraftSource(StrEnum):
    MANUAL = "manual"
    LLM = "llm"


class DraftState(StrEnum):
    EDITING = "editing"
    SUBMITTED = "submitted"
    DISCARDED = "discarded"


class AssistantRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class OutboxDeliveryState(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    ACKED = "acked"
    FAILED = "failed"


class InboundResult(StrEnum):
    """进站消息处理结果（写入 inbound_receipts.result_code）。"""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    LATE = "late"
    UNBOUND = "unbound"
    UNHANDLED = "unhandled"
    BOUND = "bound"


class TaskEventType(StrEnum):
    CREATED = "created"
    DELIVERED = "delivered"
    REPLY_SUBMITTED = "reply_submitted"
    REPLY_REJECTED_LATE = "reply_rejected_late"
    FALLBACK = "fallback"
    STREAM = "stream"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ActorType(StrEnum):
    SYSTEM = "system"
    USER = "user"
    IM = "im"
    UPSTREAM = "upstream"
    CALLER = "caller"


class ToolExecutionState(StrEnum):
    """工具沙箱执行状态（M12）。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    LIMIT_EXCEEDED = "limit_exceeded"


class AuditResult(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"


class AuditAction(StrEnum):
    """审计动作稳定枚举（M3 起逐项扩展）。"""

    ADMIN_CREATED = "admin.created"
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DISABLED = "user.disabled"
    USER_ENABLED = "user.enabled"
    USER_PASSWORD_RESET = "user.password_reset"
    USER_PASSWORD_CHANGED = "user.password_changed"
    INVITATION_CREATED = "invitation.created"
    INVITATION_UPDATED = "invitation.updated"
    INVITATION_REVOKED = "invitation.revoked"
    INVITATION_DELETED = "invitation.deleted"
    CONNECTION_CREATED = "connection.created"
    CONNECTION_UPDATED = "connection.updated"
    CONNECTION_STARTED = "connection.started"
    CONNECTION_STOPPED = "connection.stopped"
    CONNECTION_APPLIED = "connection.applied"
    CONNECTION_DELETED = "connection.deleted"
    CONNECTION_BOUND = "connection.bound"
    CONNECTION_LOGIN_STARTED = "connection.login_started"
    LLM_CONFIG_CREATED = "llm_config.created"
    LLM_CONFIG_UPDATED = "llm_config.updated"
    LLM_CONFIG_DELETED = "llm_config.deleted"
    LLM_DRAFT_GENERATED = "llm_draft.generated"
    LLM_FORWARD_COMPLETED = "llm_forward.completed"
    LLM_FORWARD_FAILED = "llm_forward.failed"
    FAKE_MODEL_CREATED = "fake_model.created"
    FAKE_MODEL_UPDATED = "fake_model.updated"
    FAKE_MODEL_DELETED = "fake_model.deleted"
    MODEL_GROUP_CREATED = "model_group.created"
    MODEL_GROUP_UPDATED = "model_group.updated"
    MODEL_GROUP_DELETED = "model_group.deleted"
    API_KEY_CREATED = "api_key.created"
    API_KEY_UPDATED = "api_key.updated"
    API_KEY_DELETED = "api_key.deleted"
    TASK_REPLY_SUBMITTED = "task.reply_submitted"
    TOOL_WHITELIST_CREATED = "tool_whitelist.created"
    TOOL_WHITELIST_UPDATED = "tool_whitelist.updated"
    TOOL_WHITELIST_DELETED = "tool_whitelist.deleted"
    TOOL_EXECUTED = "tool.executed"
    TOOL_EXECUTION_DENIED = "tool.execution_denied"
