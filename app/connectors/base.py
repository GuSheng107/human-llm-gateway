from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from ..enums import ConnectorStatus


@dataclass(frozen=True)
class OutboundTask:
    task_id: str
    text: str
    target: str = ""


@dataclass(frozen=True)
class InboundMessage:
    connector_id: int
    sender_id: str
    text: str
    conversation_id: str = ""
    external_message_id: str = ""
    reply_to_task_id: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


InboundHandler = Callable[[InboundMessage], Awaitable[None]]
StateHandler = Callable[[int, dict[str, Any]], None]
RUNTIME_STATUS_KEY = "_runtime_status"
RUNTIME_ERROR_KEY = "_runtime_error"


class Connector(Protocol):
    async def start(self, config: dict[str, Any]) -> None: ...
    async def stop(self) -> None: ...
    async def status(self) -> ConnectorStatus: ...
    async def health(self) -> dict[str, Any]: ...
    async def send_task(self, task: OutboundTask) -> dict[str, Any]: ...
