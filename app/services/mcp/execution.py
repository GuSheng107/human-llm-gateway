"""HTTP MCP 与内置助手共用的只读调用、参数校验、脱敏和审计边界。"""

from __future__ import annotations

import json
import time
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from ...core.logging import log_event
from ...domain.enums import AuditAction, AuditResult
from ...domain.errors import DomainError
from ...repositories.models import User
from ...repositories.system import AuditRepository
from ..assistant.redaction import redact_schema, redact_value
from .tools import get_mcp_tool

MAX_ARGUMENT_BYTES = 64 * 1024
MAX_RESULT_BYTES = 32 * 1024


class McpParameterError(ValueError):
    """仅包含可公开的固定错误文案，不包含输入值或内部异常。"""


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def execute_tool(session: Session, user: User, name: str, arguments: object) -> dict[str, Any]:
    """调用注册的只读 handler；调用方负责提交审计并在网络 I/O 前释放事务。"""
    started = time.monotonic()
    tool = get_mcp_tool(name) if isinstance(name, str) else None
    result_state = AuditResult.FAILED
    error_code = "tool_failed"
    hits = 0
    try:
        current_user = session.get(User, user.id, populate_existing=True)
        if current_user is None or not current_user.is_active:
            result_state = AuditResult.DENIED
            error_code = "access_denied"
            return _error("用户不可用")
        if tool is None:
            error_code = "unknown_tool"
            raise McpParameterError("Unknown tool")
        if not isinstance(arguments, dict):
            error_code = "invalid_arguments"
            raise McpParameterError("Tool arguments must be an object")
        try:
            encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise McpParameterError("Invalid tool arguments") from None
        if len(encoded.encode()) > MAX_ARGUMENT_BYTES:
            raise McpParameterError("Tool arguments exceed the size limit")
        if next(Draft202012Validator(tool.input_schema).iter_errors(arguments), None):
            error_code = "invalid_arguments"
            raise McpParameterError("Tool arguments do not match inputSchema")
        try:
            with session.begin_nested():
                raw = tool.handler(session, current_user, arguments)
        except DomainError as exc:
            result_state = (
                AuditResult.DENIED if exc.status_code in (403, 404) else AuditResult.FAILED
            )
            error_code = exc.code.value
            raw = _error(
                "资源不存在或无权访问" if exc.status_code in (403, 404) else "工具调用失败"
            )
        except Exception:  # noqa: BLE001 - 内部异常不能发送给模型或 HTTP 客户端。
            raw = _error("工具调用失败")
        clean, hits = redact_value(raw)
        # 只有固定注册工具的固定路径是 Schema；不能从用户参数的形状推断。
        if tool.name == "get_caller_tool_schema" and not raw.get("isError"):
            for original, sanitized in zip(raw.get("content", []), clean.get("content", [])):
                if original.get("type") != "text":
                    continue
                try:
                    original_data = json.loads(original["text"])
                    safe_data = json.loads(sanitized["text"])
                    schema = original_data["tool"]["input_schema"]
                except (KeyError, TypeError, ValueError):
                    continue
                if isinstance(schema, dict):
                    safe_data["tool"]["input_schema"] = redact_schema(schema)
                    sanitized["text"] = json.dumps(safe_data, ensure_ascii=False)
        if not isinstance(clean, dict) or not isinstance(clean.get("content"), list):
            clean = _error("工具返回格式无效")
        if len(json.dumps(clean, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
            clean = _error("工具结果超出大小限制，请缩小查询范围")
            error_code = "result_too_large"
        if not clean.get("isError"):
            result_state = AuditResult.SUCCESS
            error_code = ""
        return clean
    finally:
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        # 未知工具名/参数键也属于模型输入，不写入日志以免变成凭据通道。
        safe_name = tool.name if tool else "unknown"
        metadata = {
            "argument_count": len(arguments) if isinstance(arguments, dict) else 0,
            "duration_ms": duration_ms,
            "redacted_count": hits,
            "error_code": error_code,
        }
        AuditRepository().add(
            session,
            action=AuditAction.MCP_TOOL_CALLED,
            resource_type="mcp_tool",
            resource_id=safe_name,
            actor_user_id=user.id,
            owner_user_id=user.id,
            result=result_state,
            metadata=metadata,
        )
        log_event(
            "info" if result_state is AuditResult.SUCCESS else "warning",
            "mcp.tool_called",
            "只读 MCP 工具调用",
            user_id=user.id,
            tool_name=safe_name,
            result=result_state.value,
            **metadata,
        )
