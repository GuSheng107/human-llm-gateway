"""Caller Tool：外部请求通过 tools 字段声明、由调用方 IDE 执行的工具。

与旧平台工具完全无关：本模块没有命令模板、白名单、沙箱或任何执行路径。
CallerToolCatalog 是从当前任务的原始请求派生的只读视图，只承载协议工具
定义、tool_choice 策略和并行约束，供回复工作台展示、Tool Call 校验与
参数生成使用。

术语（AGENTS.md §1）：
- Caller Tool：调用方声明、调用方执行；网关只校验、保存、渲染和返回。
- Tool Call：网关回复中符合外部协议的真实工具调用数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .enums import InferenceProtocol
from .errors import DomainError, DomainErrorCode


class CallerToolChoice(StrEnum):
    """归一化的 tool_choice 语义（跨协议统一）。

    - auto：可自由决定是否调用（回复可以完全没有 Tool Call）。
    - none：禁止任何 Tool Call。
    - required：至少一个 Tool Call（仅提交期强制）。
    - named：必须调用指定工具且至少一次（仅提交期强制）。
    """

    AUTO = "auto"
    NONE = "none"
    REQUIRED = "required"
    NAMED = "named"


@dataclass(frozen=True)
class CallerToolDefinition:
    """单个 Caller Tool 定义。

    source_type 保留协议原始工具类型（如 ``function`` / ``custom`` /
    ``server_tool`` / ``web_search_preview``）。非 function-like 类型仅用于
    展示，人工/LLM 不得生成此类调用（``is_generatable`` 为 False）。
    """

    name: str
    description: str | None
    input_schema: dict[str, Any]
    source_type: str

    @property
    def is_generatable(self) -> bool:
        return self.source_type in {"function", "custom"}


@dataclass(frozen=True)
class CallerToolPolicy:
    choice: CallerToolChoice = CallerToolChoice.AUTO
    required_name: str | None = None
    parallel_allowed: bool = True


@dataclass(frozen=True)
class CallerToolCatalog:
    definitions: tuple[CallerToolDefinition, ...]
    policy: CallerToolPolicy

    @property
    def names(self) -> frozenset[str]:
        return frozenset(tool.name for tool in self.definitions)

    @property
    def is_empty(self) -> bool:
        return not self.definitions

    def get(self, name: str) -> CallerToolDefinition | None:
        for tool in self.definitions:
            if tool.name == name:
                return tool
        return None


# ---------------------------------------------------------------------------
# 协议解析（三协议各自解析原始字段并生成同一 Catalog）
# ---------------------------------------------------------------------------


def _chat_tool_def(item: dict[str, Any]) -> CallerToolDefinition | None:
    """OpenAI Chat：{type: function, function: {name, description, parameters}}。"""
    function = item.get("function")
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        return None
    schema = function.get("parameters")
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    return CallerToolDefinition(
        name=function["name"],
        description=function.get("description"),
        input_schema=schema,
        source_type=str(item.get("type") or "function"),
    )


def _responses_tool_def(item: dict[str, Any]) -> CallerToolDefinition | None:
    """OpenAI Responses：{type: function, name, description, parameters}。"""
    if not isinstance(item.get("name"), str):
        return None
    schema = item.get("parameters")
    return CallerToolDefinition(
        name=item["name"],
        description=item.get("description"),
        input_schema=schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
        source_type=str(item.get("type") or "function"),
    )


def _anthropic_tool_def(item: dict[str, Any]) -> CallerToolDefinition | None:
    """Anthropic：{name, description, input_schema}，type 可选（custom/server_tool）。"""
    if not isinstance(item.get("name"), str):
        return None
    schema = item.get("input_schema")
    return CallerToolDefinition(
        name=item["name"],
        description=item.get("description"),
        input_schema=schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
        source_type=str(item.get("type") or "custom"),
    )


def _parse_tools(protocol: InferenceProtocol, tools: Any) -> list[CallerToolDefinition]:
    if not isinstance(tools, list):
        return []
    parser = {
        InferenceProtocol.OPENAI_CHAT: _chat_tool_def,
        InferenceProtocol.OPENAI_RESPONSES: _responses_tool_def,
        InferenceProtocol.ANTHROPIC_MESSAGES: _anthropic_tool_def,
    }[protocol]
    definitions: list[CallerToolDefinition] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        tool = parser(item)
        if tool is not None:
            definitions.append(tool)
    return definitions


def _named(name: Any) -> CallerToolPolicy:
    return CallerToolPolicy(
        choice=CallerToolChoice.NAMED, required_name=name if isinstance(name, str) else None
    )


def _parse_chat_choice(raw: Any) -> CallerToolPolicy:
    if isinstance(raw, dict):
        function = raw.get("function")
        return _named(function.get("name") if isinstance(function, dict) else raw.get("name"))
    choice = str(raw or "auto").lower()
    mapping = {
        "auto": CallerToolChoice.AUTO,
        "none": CallerToolChoice.NONE,
        "required": CallerToolChoice.REQUIRED,
    }
    return CallerToolPolicy(choice=mapping.get(choice, CallerToolChoice.AUTO))


def _parse_responses_choice(raw: Any) -> CallerToolPolicy:
    if isinstance(raw, dict):
        return _named(raw.get("name"))
    choice = str(raw or "auto").lower()
    mapping = {
        "auto": CallerToolChoice.AUTO,
        "none": CallerToolChoice.NONE,
        "required": CallerToolChoice.REQUIRED,
    }
    return CallerToolPolicy(choice=mapping.get(choice, CallerToolChoice.AUTO))


def _parse_anthropic_choice(raw: Any) -> CallerToolPolicy:
    if isinstance(raw, dict):
        return _named(raw.get("name"))
    choice = str(raw or "auto").lower()
    mapping = {
        "auto": CallerToolChoice.AUTO,
        "any": CallerToolChoice.REQUIRED,
        "tool": CallerToolChoice.NAMED,
    }
    return CallerToolPolicy(choice=mapping.get(choice, CallerToolChoice.AUTO))


def _parse_policy(
    protocol: InferenceProtocol, tool_choice: Any, raw_payload: dict[str, Any]
) -> CallerToolPolicy:
    if protocol is InferenceProtocol.OPENAI_CHAT:
        policy = _parse_chat_choice(tool_choice)
        parallel = raw_payload.get("parallel_tool_calls")
    elif protocol is InferenceProtocol.OPENAI_RESPONSES:
        policy = _parse_responses_choice(tool_choice)
        parallel = raw_payload.get("parallel_tool_calls")
    else:
        policy = _parse_anthropic_choice(tool_choice)
        parallel = not bool(raw_payload.get("disable_parallel_tool_use"))
    if isinstance(parallel, bool) and not parallel:
        return CallerToolPolicy(
            choice=policy.choice, required_name=policy.required_name, parallel_allowed=False
        )
    return policy


def build_caller_tool_catalog(
    protocol: InferenceProtocol, raw_payload: dict[str, Any]
) -> CallerToolCatalog:
    """从原始请求构造 CallerToolCatalog（只读视图，不新增平台工具表）。"""
    definitions = _parse_tools(protocol, raw_payload.get("tools"))
    policy = _parse_policy(protocol, raw_payload.get("tool_choice"), raw_payload)
    return CallerToolCatalog(definitions=tuple(definitions), policy=policy)


def assert_unique_tool_names(definitions: list[CallerToolDefinition]) -> None:
    """请求解析阶段：工具定义名称必须唯一，歧义直接协议兼容 400。

    注意：这是对「请求声明的工具定义」的去重要求；同一条回复里多次调用
    同一个工具是允许的（每次调用有独立 call ID）。
    """
    seen: set[str] = set()
    for tool in definitions:
        if tool.name in seen:
            raise DomainError(
                DomainErrorCode.INVALID_REQUEST,
                f"tools 中存在重复的工具名称: {tool.name}",
                status_code=400,
            )
        seen.add(tool.name)
