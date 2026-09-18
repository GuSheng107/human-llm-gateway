"""函数工具定义、指定工具与并行约束的跨协议转换。"""

from __future__ import annotations

from typing import Any

from .cross_messages import check_fields, required_string, unsupported


def tools_to_chat(normalized: dict[str, Any]) -> list[dict[str, Any]] | None:
    tools = normalized.get("tools")
    if tools is None:
        return None
    if not isinstance(tools, list):
        raise unsupported("tools", "must be an array")
    converted = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise unsupported("tools", "invalid definition")
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            check_fields(tool, {"type", "function"}, "tool")
            fn = dict(tool["function"])
            check_fields(fn, {"name", "description", "parameters", "strict"}, "function")
        elif tool.get("type") == "function":
            check_fields(tool, {"type", "name", "description", "parameters", "strict"}, "tool")
            fn = {key: value for key, value in tool.items() if key != "type"}
        elif "input_schema" in tool and tool.get("type") in {None, "custom"}:
            check_fields(tool, {"type", "name", "description", "input_schema"}, "tool")
            fn = {"name": tool.get("name"), "parameters": tool["input_schema"]}
            if tool.get("description") is not None:
                fn["description"] = tool["description"]
        else:
            raise unsupported("tools", "managed/non-function tools have no equivalent")
        required_string(fn.get("name"), "tool.name")
        if "parameters" in fn and not isinstance(fn["parameters"], dict):
            raise unsupported("tool.parameters", "must be a schema object")
        if fn.get("strict") is not None and not isinstance(fn["strict"], bool):
            raise unsupported("tool.strict", "must be boolean")
        converted.append({"type": "function", "function": fn})
    return converted


def tools_to_anthropic(normalized: dict[str, Any]) -> list[dict[str, Any]] | None:
    converted = []
    for tool in tools_to_chat(normalized) or []:
        fn = tool["function"]
        if fn.get("strict") is True:
            raise unsupported("tool.strict", "strict enforcement has no declared equivalent")
        result = {"name": fn["name"], "input_schema": fn.get("parameters", {"type": "object"})}
        if fn.get("description") is not None:
            result["description"] = fn["description"]
        converted.append(result)
    return converted or None


def tool_choice_to_chat(normalized: dict[str, Any]) -> Any:
    choice = normalized.get("tool_choice")
    if choice is None:
        return None
    if isinstance(choice, str):
        if choice in {"auto", "none", "required"}:
            return choice
        raise unsupported("tool_choice", "no equivalent value")
    if not isinstance(choice, dict):
        raise unsupported("tool_choice", "must be a string or object")
    check_fields(choice, {"type", "name", "function", "disable_parallel_tool_use"}, "tool_choice")
    kind = choice.get("type")
    if kind in {"auto", "none", "any"}:
        if choice.get("name") is not None or choice.get("function") is not None:
            raise unsupported("tool_choice", "unexpected named tool")
        return "required" if kind == "any" else kind
    if kind in {"function", "tool"}:
        if isinstance(choice.get("function"), dict):
            check_fields(choice["function"], {"name"}, "tool_choice.function")
            name = choice["function"].get("name")
        else:
            name = choice.get("name")
        return {"type": "function", "function": {"name": required_string(name, "tool_choice.name")}}
    raise unsupported("tool_choice", "no equivalent value")


def tool_choice_to_anthropic(normalized: dict[str, Any]) -> Any:
    choice = tool_choice_to_chat(normalized)
    if choice is None:
        return None
    if isinstance(choice, str):
        return {"type": "any" if choice == "required" else choice}
    return {"type": "tool", "name": choice["function"]["name"]}


def parallel_allowed(normalized: dict[str, Any]) -> bool | None:
    value = (normalized.get("options") or {}).get("parallel_tool_calls")
    if value is not None and not isinstance(value, bool):
        raise unsupported("parallel_tool_calls", "must be boolean")
    choice = normalized.get("tool_choice")
    if isinstance(choice, dict) and choice.get("disable_parallel_tool_use") is not None:
        disabled = choice["disable_parallel_tool_use"]
        if not isinstance(disabled, bool):
            raise unsupported("disable_parallel_tool_use", "must be boolean")
        if value is not None and value == disabled:
            raise unsupported("parallel_tool_calls", "conflicting parallel controls")
        return not disabled
    return value
