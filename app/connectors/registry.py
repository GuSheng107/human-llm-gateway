"""连接器平台注册表。

注册表声明平台元数据、配置 Schema 和工厂；核心服务只按注册表创建实例，
不出现 `if platform == ...` 的业务分支（docs/ARCHITECTURE.md §8）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.connections import ConnectorError
from .base import Connector


@dataclass(frozen=True)
class ConfigField:
    name: str
    label: str
    field_type: str = "string"  # string | url | int | boolean
    required: bool = False
    secret: bool = False
    description: str = ""
    user_configurable: bool = True


@dataclass(frozen=True)
class PlatformSpec:
    code: str
    label: str
    description: str
    kind: str  # server: 服务端接收进站；client: 主动连接外部平台
    supports_delivery: bool  # 是否能把任务包投递回外部平台
    supports_login: bool = False  # 是否支持交互式登录（扫码等）
    requires_binding: bool = False  # 启用前是否必须完成扫码或消息绑定
    # 固定的人工绑定命令；仅需要在平台消息中完成绑定的连接器配置。
    # 命令由平台注册表提供，避免服务层按平台散落硬编码。
    binding_command: str | None = None
    config_fields: tuple[ConfigField, ...] = ()

    def config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": f.name,
                "label": f.label,
                "type": f.field_type,
                "required": f.required,
                "secret": f.secret,
                "description": f.description,
            }
            for f in self.config_fields
            if f.user_configurable
        ]

    def user_config_field_names(self) -> set[str]:
        return {field.name for field in self.config_fields if field.user_configurable}


class ConnectorRegistry:
    """平台注册表：平台定义与工厂实现解耦。"""

    def __init__(self) -> None:
        self._specs: dict[str, PlatformSpec] = {}
        self._factories: dict[str, type[Connector]] = {}

    def register(self, spec: PlatformSpec, factory: type[Connector]) -> None:
        if spec.code in self._specs:
            raise ValueError(f"平台重复注册: {spec.code}")
        self._specs[spec.code] = spec
        self._factories[spec.code] = factory

    def list_specs(self) -> list[PlatformSpec]:
        return [self._specs[code] for code in sorted(self._specs)]

    def get_spec(self, code: str) -> PlatformSpec | None:
        return self._specs.get(code)

    def require_spec(self, code: str) -> PlatformSpec:
        spec = self._specs.get(code)
        if spec is None:
            raise ConnectorError("config_invalid", f"未知平台: {code}")
        return spec

    def create(self, code: str, ctx) -> Connector:
        factory = self._factories.get(code)
        if factory is None:
            raise ConnectorError("config_invalid", f"未知平台: {code}")
        return factory(ctx)

    def validate_config(self, code: str, config: dict[str, Any]) -> list[str]:
        """按平台 Schema 校验配置（类型、必填），返回问题列表。"""
        spec = self._specs.get(code)
        if spec is None:
            return [f"未知平台: {code}"]
        problems: list[str] = []
        for f in spec.config_fields:
            value = config.get(f.name)
            missing = value is None or (isinstance(value, str) and value == "")
            if f.required and missing:
                problems.append(f"缺少必填配置: {f.label}")
                continue
            if missing:
                continue
            if f.field_type == "int" and not isinstance(value, int):
                problems.append(f"配置 {f.label} 必须是整数")
            elif f.field_type == "boolean" and not isinstance(value, bool):
                problems.append(f"配置 {f.label} 必须是布尔值")
            elif (
                isinstance(value, str)
                and f.field_type == "url"
                and not value.startswith(("http://", "https://"))
            ):
                problems.append(f"配置 {f.label} 必须是 http(s) URL")
            elif not isinstance(value, (str, int, float, bool)):
                problems.append(f"配置 {f.label} 类型不合法")
        extra = set(config) - {f.name for f in spec.config_fields}
        if extra:
            problems.append(f"存在平台不支持的配置字段: {', '.join(sorted(extra))}")
        return problems


def build_default_registry() -> ConnectorRegistry:
    """装配五个目标平台的注册表（docs/ROADMAP.md M4）。"""
    from .implementations.http_poll import HttpPollConnector
    from .implementations.webhook import WebhookConnector
    from .implementations.wecom_aibot import WeComAibotConnector
    from .implementations.wecom_ilink import WeComIlinkConnector
    from .implementations.ws_server import WebSocketServerConnector

    registry = ConnectorRegistry()
    registry.register(
        PlatformSpec(
            code="wecom_ilink",
            label="微信 iLink",
            description="微信 iLink 机器人：扫码登录、长轮询监听、发送消息。",
            kind="client",
            supports_delivery=True,
            supports_login=True,
            requires_binding=True,
            config_fields=(
                # 以下字段只供扫码流程在服务端持久化，不暴露给用户配置。
                ConfigField(
                    name="token",
                    label="iLink Token",
                    secret=True,
                    description="扫码登录成功后由服务端自动保存。",
                    user_configurable=False,
                ),
                ConfigField(
                    name="base_url",
                    label="服务地址",
                    field_type="url",
                    description="扫码登录流程使用的服务地址。",
                    user_configurable=False,
                ),
            ),
        ),
        WeComIlinkConnector,
    )
    registry.register(
        PlatformSpec(
            code="wecom_aibot",
            label="企业微信智能机器人",
            description="企微智能机器人 WebSocket 长连接（wecom-aibot-sdk）。",
            kind="client",
            supports_delivery=True,
            requires_binding=True,
            binding_command="connect mycom",
            config_fields=(
                ConfigField(name="bot_id", label="Bot ID", required=True),
                ConfigField(name="secret", label="Bot Secret", required=True, secret=True),
            ),
        ),
        WeComAibotConnector,
    )
    registry.register(
        PlatformSpec(
            code="webhook",
            label="自定义 Webhook",
            description="服务端接收入站消息，按配置 URL 推送任务。",
            kind="server",
            supports_delivery=True,
            binding_command="connect webhook",
            config_fields=(
                ConfigField(name="inbound_token", label="入站 Token", required=True, secret=True),
                ConfigField(name="outbound_url", label="推送 URL", field_type="url", required=True),
                ConfigField(name="outbound_token", label="推送 Token", secret=True),
            ),
        ),
        WebhookConnector,
    )
    registry.register(
        PlatformSpec(
            code="websocket",
            label="自定义 WebSocket",
            description="带连接 Token 的双向 WebSocket 会话。",
            kind="server",
            supports_delivery=True,
            binding_command="connect websocket",
            config_fields=(
                ConfigField(
                    name="connection_token", label="连接 Token", required=True, secret=True
                ),
            ),
        ),
        WebSocketServerConnector,
    )
    registry.register(
        PlatformSpec(
            code="http_poll",
            label="自定义 HTTP 轮询",
            description="按 cursor 拉取任务、提交回复和可选 ACK。",
            kind="server",
            supports_delivery=False,
            config_fields=(
                ConfigField(name="pull_token", label="拉取 Token", required=True, secret=True),
            ),
        ),
        HttpPollConnector,
    )
    return registry


# 应用级默认注册表；测试可构建独立实例。
default_registry = build_default_registry()
