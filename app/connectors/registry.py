from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..enums import ConnectorPlatform
from .base import InboundHandler, StateHandler
from .http_poll import HttpPollConnector
from .ilink import WeChatILinkConnector
from .webhook import WebhookConnector
from .websocket import WebSocketConnector
from .wecom import WeComConnector


@dataclass(frozen=True, slots=True)
class ConfigField:
    key: str
    label: str
    kind: str = "text"
    required: bool = False
    secret: bool = False
    placeholder: str = ""
    default: str | int | float | bool | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "required": self.required,
            "secret": self.secret,
            "placeholder": self.placeholder,
            "default": self.default,
        }


class ConnectorFactory(Protocol):
    def __call__(
        self,
        connector_id: int,
        config: dict[str, Any],
        on_message: InboundHandler,
        on_state: StateHandler,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class ConnectorDefinition:
    platform: ConnectorPlatform
    label: str
    description: str
    factory: ConnectorFactory
    fields: tuple[ConfigField, ...] = ()
    capabilities: frozenset[str] = field(default_factory=frozenset)
    enabled: bool = True

    def validate(self, config: dict[str, Any]) -> list[str]:
        missing = [item.label for item in self.fields if item.required and not config.get(item.key)]
        return [f"缺少必填配置：{label}" for label in missing]

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.platform.value,
            "label": self.label,
            "description": self.description,
            "capabilities": sorted(self.capabilities),
            "enabled": self.enabled,
            "fields": [item.public_dict() for item in self.fields],
        }


class ConnectorRegistry:
    """连接器注册表；新增 IM 只需注册定义，不修改 Manager 分支。"""

    def __init__(self) -> None:
        self._definitions: dict[ConnectorPlatform, ConnectorDefinition] = {}

    def register(self, definition: ConnectorDefinition) -> None:
        self._definitions[definition.platform] = definition

    def get(self, platform: ConnectorPlatform) -> ConnectorDefinition:
        try:
            return self._definitions[platform]
        except KeyError as exc:
            raise LookupError(f"未注册的 IM 平台：{platform.value}") from exc

    def all(self, *, include_disabled: bool = False) -> list[ConnectorDefinition]:
        definitions = list(self._definitions.values())
        if include_disabled:
            return definitions
        return [item for item in definitions if item.enabled]

    def create(
        self,
        platform: ConnectorPlatform,
        connector_id: int,
        config: dict[str, Any],
        on_message: InboundHandler,
        on_state: StateHandler,
    ) -> Any:
        definition = self.get(platform)
        errors = definition.validate(config)
        if errors:
            raise ValueError("；".join(errors))
        return definition.factory(connector_id, config, on_message, on_state)


def build_default_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(
        ConnectorDefinition(
            ConnectorPlatform.WECHAT_ILINK,
            "微信 iLink",
            "扫码登录自己的微信 Bot，再发送绑定码确认操作者身份。",
            lambda connector_id, config, on_message, on_state: WeChatILinkConnector(
                connector_id, config, on_message, on_state
            ),
            capabilities=frozenset({"login", "binding", "inbound", "outbound"}),
        )
    )
    registry.register(
        ConnectorDefinition(
            ConnectorPlatform.WECOM,
            "企业微信智能机器人",
            "通过企微 AI Bot SDK 长连接收发消息。",
            lambda connector_id, config, on_message, on_state: WeComConnector(
                connector_id, config, on_message, on_state
            ),
            fields=(
                ConfigField("bot_id", "Bot ID", required=True),
                ConfigField("secret", "Bot Secret", required=True, secret=True),
                ConfigField(
                    "websocket_url",
                    "WebSocket 地址",
                    placeholder="留空使用 SDK 默认地址",
                ),
            ),
            capabilities=frozenset({"binding", "inbound", "outbound", "long_connection"}),
        )
    )
    registry.register(
        ConnectorDefinition(
            ConnectorPlatform.WEBHOOK,
            "自定义 Webhook",
            "外部服务通过 HTTP 回调收取任务并提交完整回复。",
            lambda connector_id, config, on_message, on_state: WebhookConnector(
                connector_id, config
            ),
            fields=(
                ConfigField("inbound_token", "进站 Token", secret=True),
                ConfigField("target_url", "出站 Webhook URL", kind="url"),
                ConfigField("headers", "自定义请求头", kind="json"),
            ),
            capabilities=frozenset({"binding", "inbound", "outbound"}),
        )
    )
    registry.register(
        ConnectorDefinition(
            ConnectorPlatform.WEBSOCKET,
            "自定义 WebSocket",
            "客户端连接网关 WebSocket，双向交换任务与完整回复。",
            lambda connector_id, config, on_message, on_state: WebSocketConnector(
                connector_id, config
            ),
            fields=(ConfigField("auth_token", "连接 Token", secret=True),),
            capabilities=frozenset({"binding", "inbound", "outbound"}),
        )
    )
    registry.register(
        ConnectorDefinition(
            ConnectorPlatform.HTTP,
            "自定义 HTTP 轮询",
            "轮询外部消息源，支持游标推进和处理确认。",
            lambda connector_id, config, on_message, on_state: HttpPollConnector(
                connector_id, config, on_message, on_state
            ),
            fields=(
                ConfigField("inbound_url", "轮询 URL", kind="url"),
                ConfigField("target_url", "任务推送 URL", kind="url"),
                ConfigField("ack_url", "消息确认 URL", kind="url"),
                ConfigField("headers", "自定义请求头", kind="json"),
                ConfigField("poll_interval_seconds", "轮询间隔（秒）", kind="number", default=5),
            ),
            capabilities=frozenset({"binding", "inbound", "outbound", "cursor", "ack"}),
        )
    )
    return registry


connector_registry = build_default_registry()
