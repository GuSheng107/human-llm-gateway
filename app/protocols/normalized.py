"""三协议共享的规范化请求结构与历史上下文展开预算。

规范化请求 JSON 结构（写入 request_tasks.normalized_request_json）：

```json
{
  "context": [...],        // 展开后的上下文条目（message / 输出项）
  "instructions": "...",   // 可选系统指令（OpenAI 并入 messages、Anthropic 顶级 system）
  "system_blocks": [...],  // Anthropic system 内容块原样保留
  "tools": [...],          // 原始 tools 数组（不执行，仅透传/展示）
  "tool_choice": ...,      // 原始 tool choice
  "options": {...},        // 采样等其余透传字段
  "messages": [...],       // 原始 messages（Chat/Anthropic）
  "input": ...             // 原始 input（Responses）
}
```

展开唯一语义：任务的 context 已包含其父链全部历史（祖先任务在创建时
已完成等价展开），因此展开只追加「历史任务的回复项」，不重复拼接祖先
上下文（docs/API_CONTRACT.md §12.5 / docs/DATABASE.md §7.1）。
"""

from __future__ import annotations

import json
from typing import Any

from ..core.constants import (
    MAX_EXPANDED_CONTEXT_BYTES,
    MAX_EXPANDED_ITEMS,
)
from ..domain.errors import DomainError, DomainErrorCode


def decode_object(raw: bytes, *, label: str = "请求体") -> dict[str, Any]:
    """解析 JSON 请求体为 dict；失败返回协议层的请求格式错误。"""
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DomainError(
            DomainErrorCode.INVALID_REQUEST, f"{label}不是合法的 JSON", status_code=400
        ) from exc
    if not isinstance(payload, dict):
        raise DomainError(
            DomainErrorCode.INVALID_REQUEST, f"{label}必须是 JSON 对象", status_code=400
        )
    return payload


def require_non_empty_str(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DomainError(
            DomainErrorCode.INVALID_REQUEST, f"{field} 必须是非空字符串", status_code=400
        )
    return value


def reject_unsupported_field(payload: dict[str, Any], field: str) -> None:
    """显式提交不支持的服务端控制字段时返回 400（字段矩阵「拒绝 400」）。"""
    if field in payload and payload[field] is not None:
        raise DomainError(
            DomainErrorCode.UNSUPPORTED_PARAMETER,
            f"不支持参数 {field}",
            status_code=400,
        )


def declared_tool_names(normalized: dict[str, Any]) -> list[str]:
    """从规范化请求中提取调用方声明的工具名（OpenAI function / Anthropic 工具）。

    tools 数组条目结构兼容两种协议：
    - OpenAI/Responses: {"type":"function","function":{"name": ...}}
    - Anthropic: {"name": ..., "input_schema": ...}
    """
    names: list[str] = []
    tools = normalized.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name") or (tool.get("function", {}) or {}).get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def context_item_count(items: list[Any]) -> int:
    """规范化上下文条目计数：顶级条目各计 1 条（message 内容块合并计 1）。"""
    return len(items)


def context_json_bytes(items: list[Any]) -> int:
    """规范化展开 JSON 的 compact UTF-8 字节数（固定序列化参数）。"""
    return len(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def enforce_context_budget(items: list[Any]) -> None:
    """三重上限中的条目与字节两项；深度在服务层沿链校验。"""
    if context_item_count(items) > MAX_EXPANDED_ITEMS:
        raise DomainError(
            DomainErrorCode.CONTEXT_LENGTH_EXCEEDED,
            "展开后的上下文条目超出上限",
            status_code=400,
        )
    if context_json_bytes(items) > MAX_EXPANDED_CONTEXT_BYTES:
        raise DomainError(
            DomainErrorCode.CONTEXT_LENGTH_EXCEEDED,
            "展开后的上下文超出大小上限",
            status_code=400,
        )
