"""调用方工具声明与人工/LLM tool_call 的领域校验。

网关不执行工具，但必须保证写回调用方的 tool_call 名称、顺序和参数结构
都来自原始请求声明。校验后由服务端按数组顺序生成稳定的 call_01、
call_02……，完全忽略客户端或上游自带的调用 ID。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..protocols.normalized import declared_tool_definitions
from .errors import DomainError, DomainErrorCode
from .values import ReplyToolCall

_MAX_TOOL_CALLS = 20


def _validation_error(message: str) -> DomainError:
    return DomainError(DomainErrorCode.VALIDATION_FAILED, message, status_code=400)


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate_schema(value: Any, schema: dict[str, Any], path: str) -> None:
    """校验工具参数常用 JSON Schema 关键字。

    该网关只需要校验调用方传回的 JSON 对象，因此覆盖 type、required、
    properties、additionalProperties、items、enum、const 与组合 schema；
    未识别的描述性关键字不会被误当成业务规则。
    """
    if not isinstance(schema, dict):
        return
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(alternatives, list):
        matched = 0
        for candidate in alternatives:
            if not isinstance(candidate, dict):
                continue
            try:
                _validate_schema(value, candidate, path)
            except DomainError:
                continue
            matched += 1
        if "oneOf" in schema and matched != 1:
            raise _validation_error(f"{path} 不符合工具参数 schema")
        if "oneOf" not in schema and matched < 1:
            raise _validation_error(f"{path} 不符合工具参数 schema")
        return
    if "const" in schema and value != schema["const"]:
        raise _validation_error(f"{path} 必须等于 {schema['const']!r}")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise _validation_error(f"{path} 不在允许的枚举值内")
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(isinstance(item, str) and _matches_type(value, item) for item in expected):
            raise _validation_error(f"{path} 类型不符合工具参数 schema")
    elif isinstance(expected, str) and not _matches_type(value, expected):
        raise _validation_error(f"{path} 类型不符合工具参数 schema")

    if isinstance(value, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        if isinstance(required, list):
            missing = [name for name in required if isinstance(name, str) and name not in value]
            if missing:
                raise _validation_error(f"{path} 缺少必填参数: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            unknown = [name for name in value if name not in properties]
            if unknown:
                raise _validation_error(f"{path} 含未声明参数: {', '.join(map(str, unknown))}")
        for name, item in value.items():
            child_schema = properties.get(name)
            if isinstance(child_schema, dict):
                _validate_schema(item, child_schema, f"{path}.{name}")
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema(item, item_schema, f"{path}[{index}]")


def normalize_tool_calls(
    normalized: dict[str, Any],
    tool_calls: Sequence[ReplyToolCall],
    *,
    expected_names: Sequence[str] | None = None,
    source: str = "回复",
) -> list[ReplyToolCall]:
    """校验并规范化 tool_calls，返回服务端生成 ID 的新列表。"""
    if len(tool_calls) > _MAX_TOOL_CALLS:
        raise _validation_error(f"{source}工具调用数量不能超过 {_MAX_TOOL_CALLS}")
    definitions = declared_tool_definitions(normalized)
    by_name = {item["name"]: item for item in definitions}
    names = [call.name.strip() for call in tool_calls]
    if len(names) != len(set(names)):
        raise _validation_error(f"{source}不能重复调用同一个工具")
    if expected_names is not None:
        selected = [name.strip() for name in expected_names]
        if len(selected) != len(set(selected)):
            raise _validation_error("选中的工具不能重复")
        unknown_selected = [name for name in selected if name not in by_name]
        if unknown_selected:
            raise _validation_error(
                f"选中的工具 {'、'.join(unknown_selected)} 不在调用方声明的工具内"
            )
        if names != selected:
            raise _validation_error(
                f"{source}工具调用必须按选中顺序完整包含: {'、'.join(selected) or '无'}"
            )
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise _validation_error(
            f"工具 {'、'.join(unknown)} 不在调用方声明的工具内，{source}的 tool_call 只能引用请求声明的工具"
        )
    result: list[ReplyToolCall] = []
    for index, call in enumerate(tool_calls, start=1):
        name = names[index - 1]
        arguments = call.arguments
        if not isinstance(arguments, dict):
            raise _validation_error(f"工具 {name} 的 arguments 必须是 JSON 对象")
        schema = by_name[name].get("parameters")
        if isinstance(schema, dict):
            _validate_schema(arguments, schema, f"工具 {name} 的 arguments")
        result.append(
            ReplyToolCall(
                id=f"call_{index:02d}",
                name=name,
                arguments=dict(arguments),
            )
        )
    return result
