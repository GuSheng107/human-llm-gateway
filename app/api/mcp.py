"""MCP Streamable HTTP Transport 端点（/api/mcp/）。

实现 MCP 2025-03-26 规范的 Streamable HTTP Transport：
- POST /api/mcp/ : JSON-RPC 2.0 主请求端点
- GET  /api/mcp/ : SSE 通知流（当前返回空流，预留）
- DELETE /api/mcp/ : 会话终止（无状态，直接 200）

鉴权复用现有 Bearer token（session cookie），与助手面板共享用户身份。
所有工具调用均为只读查询，不暴露任何写/删除/提交操作。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..repositories.models import User
from ..services.mcp.server import handle_jsonrpc
from .deps import require_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.post("/")
async def mcp_post(
    request: Request,
    user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """MCP JSON-RPC 2.0 主请求端点。

    接受单个 JSON-RPC 请求或批量请求数组。
    Content-Type 为 application/json。
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            },
        )

    # 批量请求支持
    if isinstance(body, list):
        if not body:
            return JSONResponse(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Empty batch"},
                },
            )
        results = []
        for item in body:
            if not isinstance(item, dict):
                results.append({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid request"},
                })
                continue
            results.append(handle_jsonrpc(db, user, item))
        return JSONResponse(content=results)

    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid request"},
            },
        )

    result = handle_jsonrpc(db, user, body)
    return JSONResponse(content=result)


@router.get("/")
async def mcp_get(
    user: User = Depends(require_current_user),
) -> Response:
    """SSE 通知流端点（预留）。

    当前实现返回空 SSE 流。未来可用于服务端主动推送
    （如任务状态变更通知、工具列表更新等）。
    """
    async def empty_stream():
        # 发送一个初始注释行表示连接建立
        yield b": mcp stream connected\n\n"
        # 保持连接但不推送数据（客户端可随时断开）
        try:
            while True:
                await asyncio.sleep(30)
                yield b": keepalive\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        empty_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.delete("/")
async def mcp_delete(
    user: User = Depends(require_current_user),
) -> Response:
    """会话终止端点。

    当前为无状态实现，直接返回 200。
    未来若引入服务端会话状态，可在此清理。
    """
    return Response(status_code=200)
