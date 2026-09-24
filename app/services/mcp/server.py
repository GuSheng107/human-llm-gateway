"""MCP JSON-RPC 请求边界，业务工具统一走 execution。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...repositories.models import User
from .execution import McpParameterError, execute_tool
from .tools import list_mcp_tools


def error_response(rpc_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def handle_jsonrpc(session: Session, user: User, body: dict[str, Any]) -> dict[str, Any] | None:
    """处理请求；合法 notification 无响应，无效 envelope 不回显输入。"""
    rpc_id = body.get("id")
    if (
        body.get("jsonrpc") != "2.0"
        or not isinstance(body.get("method"), str)
        or isinstance(rpc_id, bool)
        or not isinstance(rpc_id, (str, int, type(None)))
        or set(body) - {"jsonrpc", "id", "method", "params"}
    ):
        return error_response(None, -32600, "Invalid request")
    method = body["method"]
    params = body.get("params", {})
    notification = "id" not in body
    if not isinstance(params, dict):
        return None if notification else error_response(rpc_id, -32602, "Invalid params")
    if notification:
        # 不执行 tools/call 通知，防止无响应执行与审计歧义。
        return None
    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "human-llm-gateway", "version": "0.6.0"},
            }
        elif method == "tools/list":
            result = {"tools": list_mcp_tools()}
        elif method == "tools/call":
            if set(params) - {"name", "arguments", "_meta"}:
                return error_response(rpc_id, -32602, "Invalid params")
            result = execute_tool(session, user, params.get("name"), params.get("arguments", {}))
        elif method == "ping":
            result = {}
        else:
            return error_response(rpc_id, -32601, "Method not found")
    except McpParameterError as exc:
        return error_response(rpc_id, -32602, str(exc))
    except Exception:  # noqa: BLE001 - 不向调用方或日志回显内部异常内容。
        return error_response(rpc_id, -32603, "Internal error")
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}
