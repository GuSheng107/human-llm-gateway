"""连接器包：导出统一接口、注册表与运行时管理器。

注意：`manager` 名保留给子模块 `app.connectors.manager`，运行时单例
导出为 `connection_manager`，避免包属性遮蔽子模块。
"""

from .base import Connector, ConnectorContext, DeliveryEnvelope, InboundMessage
from .manager import ConnectionManager
from .manager import manager as connection_manager
from .registry import ConnectorRegistry, PlatformSpec, default_registry

__all__ = [
    "ConnectionManager",
    "Connector",
    "ConnectorContext",
    "ConnectorRegistry",
    "DeliveryEnvelope",
    "InboundMessage",
    "PlatformSpec",
    "connection_manager",
    "default_registry",
]
