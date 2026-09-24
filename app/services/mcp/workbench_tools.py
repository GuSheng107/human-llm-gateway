"""所有者专用的工作台只读工具：有界请求投影、完整草稿校验、trace 摘要。"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from ...core.logging import sanitize_log_value
from ...domain.errors import DomainError
from ...domain.values import ReplyDraft, is_empty_draft
from ...repositories.models import RequestTask, User
from ...repositories.system import AppLogRepository, AuditRepository
from ...repositories.tasks import TaskRepository
from ..assistant.redaction import redact_text
from ..caller_tool_service import catalog_for_task, validate_full
from ..request_view_service import RequestViewService

_MAX_OUTPUT_BYTES = 32 * 1024
_TASK_PROPERTIES = {"task_id": {"type": "integer", "minimum": 1}}
_DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reasoning": {"type": ["string", "null"], "maxLength": 20000},
        "final_text": {"type": ["string", "null"], "maxLength": 40000},
        "tool_calls": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "name", "arguments"],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "name": {"type": "string", "minLength": 1, "maxLength": 128},
                    "arguments": {"type": "object"},
                },
            },
        },
    },
}
_SCHEMAS = {
    "get_request_view": {
        "type": "object",
        "additionalProperties": False,
        "properties": _TASK_PROPERTIES,
        "required": ["task_id"],
    },
    "validate_reply_draft": {
        "type": "object",
        "additionalProperties": False,
        "properties": {**_TASK_PROPERTIES, "draft": _DRAFT_SCHEMA},
        "required": ["task_id", "draft"],
    },
    "get_trace_summary": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"trace_id": {"type": "string", "minLength": 1, "maxLength": 64}},
        "required": ["trace_id"],
    },
}


def _result(data: dict[str, Any], *, error: bool = False) -> dict[str, Any]:
    """过滤结构化凭据及自由文本，并明确标识截断；不回传附件正文。"""
    truncated = False

    def clean(value: Any, depth: int = 0) -> Any:
        nonlocal truncated
        if depth > 12:
            truncated = True
            return "[TRUNCATED]"
        if isinstance(value, str):
            value = redact_text(sanitize_log_value(value))[0]
            value = re.sub(r"https?://[^\s\"<>]+", "[URL-OMITTED]", value)
            if len(value) > 1200:
                truncated = True
                value = value[:1200] + "[TRUNCATED]"
            return value
        if isinstance(value, dict):
            if value.get("truncated") is True:
                truncated = True
            if len(value) > 40:
                truncated = True
            return {
                redact_text(str(k))[0]: (
                    "[REDACTED]"
                    if str(k).lower() == "token" or sanitize_log_value({k: 0})[k] == "[REDACTED]"
                    else clean(v, depth + 1)
                )
                for k, v in list(value.items())[:40]
            }
        if isinstance(value, list):
            if len(value) > 20:
                truncated = True
            return [clean(v, depth + 1) for v in value[:20]]
        return value

    cleaned = clean(data)
    cleaned["truncated"] = truncated
    text = json.dumps(cleaned, ensure_ascii=False)
    if len(text.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        # 保持有效 JSON，不能在任意字节处切开整个响应。
        text = json.dumps({"truncated": True, "summary": text[:6000]}, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _check(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    pending = [(args, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > 10000:
            return _result(
                {"error": "arguments_too_complex", "message": "工具参数结构过深或过多"}, error=True
            )
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    if len(json.dumps(args, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
        return _result(
            {"error": "arguments_too_large", "message": "工具参数超过 64 KiB"}, error=True
        )
    if not Draft202012Validator(_SCHEMAS[name]).is_valid(args):
        return _result(
            {"error": "invalid_arguments", "message": "工具参数不符合声明的结构"}, error=True
        )
    return None


def _owned_task(session: Session, user: User, task_id: int) -> RequestTask | None:
    # 小助手上游不继承管理员治理权：正文始终只发给资源所有者。
    return TaskRepository().get_owned(session, task_id, user.id)


def _missing() -> dict[str, Any]:
    return _result({"error": "not_found", "message": "资源不存在或无权访问"}, error=True)


def get_request_view(session: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """请求视图仅返回文字与媒体类型摘要，不含 raw 请求、headers 或媒体地址。"""
    invalid = _check("get_request_view", args)
    if invalid:
        return invalid
    task = _owned_task(session, user, args["task_id"])
    if task is None:
        return _missing()
    view = RequestViewService().get_request_view(session, task)

    def items(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "id": item.get("id"),
                "role": item.get("role"),
                "blocks": [
                    {
                        k: block[k]
                        for k in (
                            "type",
                            "text",
                            "name",
                            "call_id",
                            "arguments",
                            "media_type",
                            "text_length",
                            "truncated",
                        )
                        if k in block
                    }
                    for block in item.get("blocks", [])
                ],
            }
            for item in values
        ]

    return _result(
        {
            "task": view["task"],
            "current_input": items(view.get("current_input", [])),
            "attached_context": items(view.get("attached_context", [])),
            "caller_system": items(view.get("caller_system", {}).get("items", [])),
            "caller_tools": view["caller_tools"],
            "attachment_count": len(view.get("attachments", [])),
            "tool_call_warning": view["tool_call_warning"],
            "read_only": True,
        }
    )


def validate_reply_draft(session: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """复用最终提交的 Caller Tool 规则；校验原始参数但不回显参数/草稿。"""
    invalid = _check("validate_reply_draft", args)
    if invalid:
        return invalid
    task = _owned_task(session, user, args["task_id"])
    if task is None:
        return _missing()
    draft = ReplyDraft.model_validate(args["draft"])
    if is_empty_draft(draft):
        return _result({"valid": False, "error": "empty_reply", "message": "回复内容不能为空"})
    try:
        validate_full(catalog_for_task(task), draft.tool_calls)
    except DomainError as exc:
        # Schema 错误可含参数值：只报告稳定错误码，避免回显任意隐私正文。
        rule = next(
            (
                label
                for marker, label in (
                    ("Schema", "参数不符合声明的输入 Schema"),
                    ("ID 重复", "同一回复的调用 ID 不能重复"),
                    ("tool_choice=required", "必须至少包含一个工具调用"),
                    ("tool_choice=none", "本次请求禁止工具调用"),
                    ("tool_choice", "只能使用本次指定的工具"),
                    ("并行", "本次请求最多允许一个工具调用"),
                    ("不在", "调用名称未在本次请求中声明"),
                )
                if marker in exc.message
            ),
            "工具结构未通过校验",
        )
        path = re.search(r"Schema（([^）]*)）", exc.message)
        return _result(
            {
                "valid": False,
                "error": exc.public_code or exc.code.value,
                "message": rule,
                "error_path": path.group(1)[:200] if path else None,
            }
        )
    view = RequestViewService().get_request_view(session, task)
    return _result(
        {
            "valid": True,
            "task_id": task.id,
            "task_state": task.state.value,
            "tool_call_warning": view["tool_call_warning"],
            "read_only": True,
            "message": "草稿结构与工具策略通过；未保存或提交，提交时仍会校验任务状态与风险确认",
        }
    )


def get_trace_summary(session: Session, user: User, args: dict[str, Any]) -> dict[str, Any]:
    """复用日志查询的所有者范围，只发固定元数据，不发 message/context/detail。"""
    invalid = _check("get_trace_summary", args)
    if invalid:
        return invalid
    trace = args["trace_id"]
    rows, total = AppLogRepository().list_page(
        session, page=1, page_size=21, request_id=trace, scope_owner_id=user.id
    )
    audits = AuditRepository().list_for_subject(
        session, subject_user_id=user.id, request_id=trace, limit=21
    )
    events = [
        {
            "kind": "app",
            "event": row.event,
            "level": row.level,
            "created_at": row.created_at.isoformat(),
            "task_id": row.task_id,
        }
        for row in rows
    ]
    events += [
        {
            "kind": "audit",
            "event": row.action,
            "level": row.result.value,
            "created_at": row.created_at.isoformat(),
        }
        for row in audits
    ]
    events.sort(key=lambda item: item["created_at"], reverse=True)
    if not events:
        return _missing()
    return _result(
        {
            "trace_id": trace,
            "events": events[:20],
            "sampled_level_counts": dict(Counter(item["level"] for item in events[:20])),
            "has_more": total > len(rows) or len(events) > 20,
            "retention_days": 7,
            "read_only": True,
        }
    )


def workbench_tool_definitions() -> list[dict[str, Any]]:
    """沿用 McpToolDef 注册接口，避免工具实现依赖注册表。"""
    return [
        {
            "name": name,
            "description": description,
            "input_schema": _SCHEMAS[name],
            "handler": handler,
        }
        for name, description, handler in (
            (
                "get_request_view",
                "读取自己的任务请求文字、上下文及工具约束的有界脱敏视图；附件仅摘要。",
                get_request_view,
            ),
            (
                "validate_reply_draft",
                "只读检查当前未保存草稿的完整工具策略；不保存、不提交、不执行调用方工具。",
                validate_reply_draft,
            ),
            (
                "get_trace_summary",
                "读取当前用户可见 trace 的最近20条事件元数据；不读取日志正文或凭据。",
                get_trace_summary,
            ),
        )
    ]
