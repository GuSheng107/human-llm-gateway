"""Caller Tool Call 统一校验服务。

所有入口共用同一套规则（AGENTS.md §5、docs/WORKBENCH_TOOL_CALL_LOG_REFACTOR_PLAN.md §8.1）：

- Web 草稿保存 / 更新 / 提交
- 手动 LLM 草稿生成结果
- 自动 LLM 转发结果（协议重写前的结构检查）
- 页面 / 小助手工具参数生成

校验分层：
- 结构校验（save/generate/tool-arguments）：ID、名称、参数 object + Schema。
- 完整校验（submit）：结构校验 + tool_choice / 并行约束。

明确不做的判断（AGENTS.md §1/§5）：名称是否像 shell/exec/delete、参数是否
包含绝对路径/内网地址/URL、工具可能产生何种副作用——网关根本不执行工具。
"""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator

from ..domain.caller_tools import (
    CallerToolCatalog,
    CallerToolChoice,
    CallerToolDefinition,
    build_caller_tool_catalog,
)
from ..domain.errors import DomainError, DomainErrorCode

_MAX_SCHEMA_ERRORS = 5


def catalog_for_task(task: Any) -> CallerToolCatalog:
    """从任务的原始请求派生 CallerToolCatalog（只读视图）。"""
    try:
        raw = json.loads(task.raw_payload_json or "{}")
    except (ValueError, TypeError):
        raw = {}
    return build_caller_tool_catalog(task.protocol, raw if isinstance(raw, dict) else {})


def _json_pointer(errors: list[Any]) -> str:
    for error in errors:
        parts = "/".join(str(part) for part in error.absolute_path)
        return f"/{parts}"
    return "/"


def _schema_errors(tool: CallerToolDefinition, arguments: dict[str, Any]) -> list[Any]:
    schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
    if not schema:
        return []
    validator = Draft202012Validator(schema)
    return list(validator.iter_errors(arguments))[:_MAX_SCHEMA_ERRORS]


def _tool_call_view(call: Any, index: int) -> dict[str, Any]:
    if isinstance(call, dict):
        return call
    try:
        return call.model_dump()
    except AttributeError:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"tool_calls[{index}] 必须是对象",
            status_code=400,
        ) from None


def validate_structural(catalog: CallerToolCatalog, tool_calls: list[Any]) -> None:
    """结构校验：名称必须声明、类型可生成、参数是 object 且符合 Schema、ID 唯一。

    不校验 tool_choice 与并行约束（提交期由 validate_full 强制）；
    同一工具允许被多次调用（每个 call 有独立 ID）。
    """
    if not tool_calls:
        return
    if catalog.is_empty:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "当前请求没有声明 Caller Tool，回复不能携带工具调用",
            status_code=400,
            public_code="caller_tools_not_available",
        )
    seen_ids: set[str] = set()
    for index, raw_call in enumerate(tool_calls):
        call = _tool_call_view(raw_call, index)
        label = f"tool_calls[{index}]"
        call_id = call.get("id")
        name = call.get("name")
        arguments = call.get("arguments")
        if not isinstance(call_id, str) or not call_id.strip():
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"{label}.id 必须是非空字符串",
                status_code=400,
            )
        if call_id in seen_ids:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"Tool Call ID 重复: {call_id}",
                status_code=400,
            )
        seen_ids.add(call_id)
        if not isinstance(name, str) or not name.strip():
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"{label}.name 必须是非空字符串",
                status_code=400,
            )
        tool = catalog.get(name)
        if tool is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"工具 {name} 不在当前请求声明的 Caller Tool 中",
                status_code=400,
                public_code="caller_tool_not_declared",
            )
        if not tool.is_generatable:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"工具 {name} 的类型不能映射为当前协议的 Tool Call，不支持生成此类调用",
                status_code=400,
                public_code="caller_tool_type_unsupported",
            )
        if not isinstance(arguments, dict):
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"{label}.arguments 必须是 JSON 对象",
                status_code=400,
            )
        errors = _schema_errors(tool, arguments)
        if errors:
            pointer = _json_pointer(errors)
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"工具 {name} 的参数不符合 Schema（{pointer}）：{errors[0].message}",
                status_code=400,
            )


def validate_full(catalog: CallerToolCatalog, tool_calls: list[Any]) -> None:
    """完整校验：结构校验 + tool_choice 与并行约束（最终提交强制）。"""
    validate_structural(catalog, tool_calls)
    if not tool_calls:
        if catalog.policy.choice is CallerToolChoice.REQUIRED:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "请求 tool_choice=required，回复必须至少包含一个 Tool Call",
                status_code=400,
            )
        if catalog.policy.choice is CallerToolChoice.NAMED:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "请求 tool_choice 指定了工具，回复必须至少包含一个 Tool Call",
                status_code=400,
            )
        return
    if catalog.policy.choice is CallerToolChoice.NONE:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "请求 tool_choice=none，回复不允许携带 Tool Call",
            status_code=400,
        )
    if catalog.policy.choice is CallerToolChoice.NAMED and catalog.policy.required_name:
        names = {call.get("name") for call in tool_calls if isinstance(call, dict)}
        if catalog.policy.required_name not in names:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"请求 tool_choice 指定了工具 {catalog.policy.required_name}，回复必须调用该工具",
                status_code=400,
            )
    if not catalog.policy.parallel_allowed and len(tool_calls) > 1:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            "请求禁用了并行工具调用，一次回复最多只能包含一个 Tool Call",
            status_code=400,
        )


def validate_tool_arguments(
    catalog: CallerToolCatalog, tool_name: str, arguments: Any
) -> CallerToolDefinition:
    """校验指定工具的参数（页面/小助手参数生成共用），返回工具定义。"""
    tool = catalog.get(tool_name)
    if tool is None:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"工具 {tool_name} 不在当前请求声明的 Caller Tool 中",
            status_code=400,
            public_code="caller_tool_not_declared",
        )
    if not tool.is_generatable:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"工具 {tool_name} 的类型不能映射为当前协议的 Tool Call",
            status_code=400,
            public_code="caller_tool_type_unsupported",
        )
    if not isinstance(arguments, dict):
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"工具 {tool_name} 的参数必须是 JSON 对象",
            status_code=400,
        )
    errors = _schema_errors(tool, arguments)
    if errors:
        pointer = _json_pointer(errors)
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"工具 {tool_name} 的参数不符合 Schema（{pointer}）：{errors[0].message}",
            status_code=400,
        )
    return tool
