from typing import Any

from ..enums import ConnectorStatus
from .base import OutboundTask


class WeChatSidecarConnector:
    """Explicit placeholder for the future Windows personal-WeChat sidecar.

    It never reports a successful delivery: the sidecar protocol is not part
    of V1 and silently using FakeConnector here would be misleading.
    """

    async def start(self, config: dict[str, Any]) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def status(self) -> ConnectorStatus:
        return ConnectorStatus.DISABLED

    async def health(self) -> dict[str, Any]:
        return {"status": ConnectorStatus.DISABLED.value, "implemented": False,
                "platform": "wechat_sidecar"}

    async def send_task(self, task: OutboundTask) -> dict[str, Any]:
        raise RuntimeError("个人微信 Sidecar 尚未实现")
