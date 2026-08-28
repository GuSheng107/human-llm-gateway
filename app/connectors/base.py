"""连接器统一能力接口与共享数据结构。

见 docs/ARCHITECTURE.md §8：连接器不直接决定任务归属或完成任务，
只收发统一命令和事件；进站处理由服务层编排。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..domain.connections import ConnectorError
from ..domain.enums import InboundResult


@dataclass
class ConnectorContext:
    """创建连接器实例所需的运行上下文（配置已解密，仅驻留内存）。"""

    connection_id: int
    owner_user_id: int
    name: str
    platform: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class InboundMessage:
    """统一进站消息。"""

    external_message_id: str
    sender_external_id: str
    text: str = ""
    binding_code: str | None = None
    reply_to_public_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryEnvelope:
    """统一任务投递包（已脱敏，不含原始完整请求）。"""

    task_public_id: str
    requested_model: str
    prompt_text: str
    owner_user_id: int
    created_at: str = ""
    reply_to_external_id: str | None = None
    context_token: str | None = None
    has_tools: bool = False
    tool_names: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_public_id,
            "model": self.requested_model,
            "prompt": self.prompt_text,
            "created_at": self.created_at,
            "tools": self.tool_names if self.has_tools else [],
        }


class InboundHandler(Protocol):
    """进站回调：由服务层注入，连接器只负责转发统一消息。"""

    async def __call__(self, connection_id: int, message: InboundMessage) -> InboundResult: ...


class Connector:
    """平台连接器基类。

    start() 之后连接器可后台收发；wait_closed() 在连接断开（或 stop）
    后返回，由连接管理器决定是否退避重连。
    """

    platform: str = ""

    def __init__(self, ctx: ConnectorContext) -> None:
        self.ctx = ctx

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> list[str]:
        """返回配置问题列表；空列表表示合法。"""
        raise NotImplementedError

    async def start(self) -> None:
        """启动连接。立即失败抛出 ConnectorError（auth/config）或网络错误。"""

    async def stop(self) -> None:
        """停止连接并释放资源；可重复调用。"""

    async def wait_closed(self) -> None:
        """连接意外断开时返回；服务端型连接器仅在 stop 后返回。"""

    async def deliver(self, envelope: DeliveryEnvelope) -> None:
        """投递任务包；失败抛出 ConnectorError。"""

    async def health(self) -> dict[str, Any]:
        """瞬时健康信息（不落库，仅用于健康接口展示）。"""
        return {"running": True}

    # 可选：交互式登录（微信 iLink 扫码等）。
    async def start_login(self) -> dict[str, Any]:
        raise ConnectorError("config_invalid", "该平台不支持交互式登录")

    async def poll_login(self) -> dict[str, Any]:
        raise ConnectorError("config_invalid", "该平台不支持交互式登录")

    async def send_reply_text(
        self, external_user_id: str, text: str, *, context_token: str | None = None
    ) -> None:
        """主动向外部用户发送文本（用于提示任务已结束等）。默认忽略。"""


InboundCallback = Callable[[int, InboundMessage], Awaitable[InboundResult]]
