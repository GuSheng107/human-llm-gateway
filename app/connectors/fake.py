from typing import Any

from .base import OutboundTask
from ..enums import ConnectorStatus


class FakeConnector:
    def __init__(self) -> None:
        self.sent: list[OutboundTask] = []
        self._status = ConnectorStatus.ONLINE

    async def start(self, config: dict[str, Any]) -> None:
        self._status = ConnectorStatus.ONLINE

    async def stop(self) -> None:
        self._status = ConnectorStatus.OFFLINE

    async def status(self) -> ConnectorStatus:
        return self._status

    async def health(self) -> dict[str, Any]:
        return {"status": self._status.value, "kind": "fake"}

    async def send_task(self, task: OutboundTask) -> dict[str, Any]:
        self.sent.append(task)
        return {"accepted": True, "task_id": task.task_id}

