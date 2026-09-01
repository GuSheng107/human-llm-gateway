"""平台内置工具定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlatformToolDefinition:
    """全新数据库初始化时写入的平台工具。"""

    name: str
    description: str
    command_template: str
    arguments_schema: dict[str, Any]
    timeout_seconds: int


PRINT_TOOL = PlatformToolDefinition(
    name="Print",
    description="在控制台输出文字内容",
    command_template="echo {text}",
    arguments_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要输出的文字",
            }
        },
        "required": ["text"],
    },
    timeout_seconds=10,
)

DEFAULT_PLATFORM_TOOLS = (PRINT_TOOL,)
