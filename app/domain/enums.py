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


class ConnectionState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    ONLINE = "online"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"


class LLMProtocol(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"


class FakeModelScope(StrEnum):
    SYSTEM = "system"
    PRIVATE = "private"


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
