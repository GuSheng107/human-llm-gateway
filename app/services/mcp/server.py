"""MCP Server 核心：JSON-RPC 2.0 分发与工具执行。

实现 MCP Streamable HTTP Transport 协议的核心方法：
- initialize: 握手
- tools/list: 列出可用工具
- tools/call: 调用工具

所有工具调用复用现有 Repository/Service，按用户权限自动限定数据范围。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ...domain.enums import AuditAction, AuditResult
from ...domain.errors import DomainError, DomainErrorCode
from ...repositories.models import User
from ...repositories.system import AuditRepository
from .tools import get_mcp_tool, list_mcp_tools

logger = logging.getLogger(__name__)

# MCP 协议版本
_MCP_PROTOCOL_VERSION = "2025-03-26"
_SERVER_INFO = {
    "name": "human-llm-gateway",
    "version": "0.6.0",
}


def handle_jsonrpc(
    session: Session,
    user: User,
    body: dict[str, Any],
) -> dict[str, Any]:
    """处理单个 JSON-RPC 2.0 请求并返回响应。

    参数:
        session: 数据库会话
        user: 当前认证用户
        body: JSON-RPC 请求体（含 method, params, id）

    返回:
        JSON-RPC 响应体
    """
    method = body.get("method", "")
    params = body.get("params") or {}
    rpc_id = body.get("id")

    try:
        if method == "initialize":
            result = _handle_initialize(params)
        elif method == "notifications/initialized":
            # 通知方法，无返回值
            return _make_response(rpc_id, None)
        elif method == "tools/list":
            result = _handle_tools_list()
        elif method == "tools/call":
            result = _handle_tools_call(session, user, params)
        elif method == "ping":
            result = {}
        else:
            return _make_error_response(rpc_id, -32601, f"Method not found: {method}")
    except DomainError as exc:
        logger.warning("MCP tool call domain error: %s", exc.message)
        return _make_error_response(rpc_id, -32000, exc.message)
    except Exception as exc:
        logger.exception("MCP unexpected error in %s", method)
        return _make_error_response(rpc_id, -32603, f"Internal error: {exc}")

    return _make_response(rpc_id, result)


def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
    """MCP initialize 握手。"""
    return {
        "protocolVersion": _MCP_PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": False},
        },
        "serverInfo": _SERVER_INFO,
    }


def _handle_tools_list() -> dict[str, Any]:
    """返回所有已注册工具的列表。"""
    return {"tools": list_mcp_tools()}


def _handle_tools_call(session: Session, user: User, params: dict[str, Any]) -> dict[str, Any]:
    """执行指定工具并返回结果。"""
    tool_name = params.get("name", "")
    arguments = params.get("arguments") or {}

    tool_def = get_mcp_tool(tool_name)
    if tool_def is None:
        raise DomainError(
            DomainErrorCode.NOT_FOUND,
            f"未知工具: {tool_name}",
            status_code=404,
        )

    # 审计记录
    audit = AuditRepository()
    audit.add(
        session,
        action=AuditAction.MCP_TOOL_CALLED,
        resource_type="mcp_tool",
        resource_id=tool_name,
        actor_user_id=user.id,
        result=AuditResult.SUCCESS,
        metadata={"arguments": arguments},
    )

    # 执行工具
    result = tool_def.handler(session, user, arguments)
    return result


# ---------------------------------------------------------------------------
# JSON-RPC 响应构造
# ---------------------------------------------------------------------------


def _make_response(rpc_id: Any, result: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": result,
    }


def _make_error_response(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": code,
            "message": message,
        },
    }
