"""汇总导入所有模型，确保 Base.metadata 完整注册。"""

from .assistant import AssistantMessage, AssistantSession
from .auth import AuthSession, InvitationCode, User
from .catalog import FakeModel, ModelGroup, ModelGroupItem
from .connections import ConnectorOutbox, ImConnection, InboundReceipt
from .keys import ApiKey, ApiKeyFakeModel
from .llm import LlmConfig
from .system import AppLog, AuditLog, SystemSetting
from .tasks import RequestTask, TaskDraft, TaskEvent

__all__ = [
    "ApiKey",
    "ApiKeyFakeModel",
    "AppLog",
    "AssistantMessage",
    "AssistantSession",
    "AuditLog",
    "AuthSession",
    "ConnectorOutbox",
    "FakeModel",
    "ImConnection",
    "InboundReceipt",
    "InvitationCode",
    "LlmConfig",
    "ModelGroup",
    "ModelGroupItem",
    "RequestTask",
    "SystemSetting",
    "TaskDraft",
    "TaskEvent",
    "User",
]
