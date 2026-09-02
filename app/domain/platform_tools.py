"""平台内置工具定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlatformToolDefinition:
    """全新数据库初始化时写入的平台工具。

    stdin_parameter 指定某个 string 参数经 stdin 喂给容器（不经 argv、
    不经 shell）；该参数的占位符不得出现在 command_template 中。
    """

    name: str
    description: str
    command_template: str
    arguments_schema: dict[str, Any]
    timeout_seconds: int
    stdin_parameter: str | None = None


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

TO_UPPER_TOOL = PlatformToolDefinition(
    name="ToUpper",
    description="把输入文本转换为大写（stdin 输入）",
    command_template="tr a-z A-Z",
    arguments_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要转换的文本（经 stdin 传入）",
            }
        },
        "required": ["text"],
    },
    timeout_seconds=10,
    stdin_parameter="text",
)

TO_LOWER_TOOL = PlatformToolDefinition(
    name="ToLower",
    description="把输入文本转换为小写（stdin 输入）",
    command_template="tr A-Z a-z",
    arguments_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要转换的文本（经 stdin 传入）",
            }
        },
        "required": ["text"],
    },
    timeout_seconds=10,
    stdin_parameter="text",
)

BASE64_ENCODE_TOOL = PlatformToolDefinition(
    name="Base64Encode",
    description="把输入文本 Base64 编码（stdin 输入）",
    command_template="base64",
    arguments_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要编码的文本（经 stdin 传入）",
            }
        },
        "required": ["text"],
    },
    timeout_seconds=10,
    stdin_parameter="text",
)

SHA256_TOOL = PlatformToolDefinition(
    name="Sha256",
    description="计算输入文本的 SHA-256 摘要（stdin 输入）",
    command_template="sha256sum",
    arguments_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要计算摘要的文本（经 stdin 传入）",
            }
        },
        "required": ["text"],
    },
    timeout_seconds=10,
    stdin_parameter="text",
)

UNAME_TOOL = PlatformToolDefinition(
    name="SystemInfo",
    description="输出容器内核与架构信息（uname -a）",
    command_template="uname -a",
    arguments_schema={
        "type": "object",
        "properties": {},
    },
    timeout_seconds=10,
)

DEFAULT_PLATFORM_TOOLS = (
    PRINT_TOOL,
    TO_UPPER_TOOL,
    TO_LOWER_TOOL,
    BASE64_ENCODE_TOOL,
    SHA256_TOOL,
    UNAME_TOOL,
)
