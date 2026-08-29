"""请求体大小上限（docs/API_CONTRACT.md §2.1、§16.3）。

在请求进入应用层之前读取并缓冲请求体；累计字节超限时不调用下游应用，
直接返回各命名空间协议兼容的 413（保证在 JSON 解析与任务创建之前）：
- `/v1/messages` -> Anthropic `request_too_large`
- 其他 `/v1/*` -> OpenAI `invalid_request_error` / `payload_too_large`
- `/api/*` -> 管理 API 统一错误结构
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..core.constants import MAX_ADMIN_REQUEST_BYTES, MAX_INFERENCE_REQUEST_BYTES


def _request_id_from_scope(scope: dict[str, Any]) -> str:
    """复用 RequestIdMiddleware 的取值规则：优先回显客户端 x-request-id。

    BodySize 注册在 RequestId 之外（最外层），413 短路时尚未触发 RequestId
    注入，因此自行解析并回写，保证 413 响应也带 x-request-id（§16.3）。
    """
    for key, value in scope.get("headers") or []:
        if key == b"x-request-id":
            return value.decode("latin-1")
    return f"req_{uuid.uuid4().hex[:24]}"


def _limit_for(path: str) -> int | None:
    if path.startswith("/v1/"):
        return MAX_INFERENCE_REQUEST_BYTES
    if path.startswith("/api/"):
        return MAX_ADMIN_REQUEST_BYTES
    return None


def _payload(path: str) -> tuple[int, dict[str, Any]]:
    if path.startswith("/v1/messages"):
        return 413, {
            "type": "error",
            "error": {"type": "request_too_large", "message": "Request exceeds the maximum size."},
        }
    if path.startswith("/v1/"):
        return 413, {
            "error": {
                "message": "Request exceeds the maximum size.",
                "type": "invalid_request_error",
                "param": None,
                "code": "payload_too_large",
            }
        }
    return 413, {
        "error": {
            "code": "payload_too_large",
            "message": "请求体超出大小上限",
            "action": "fix_input",
            "details": {},
            "request_id": "",
        }
    }


async def _send_json(send: Any, status: int, payload: dict[str, Any], *, request_id: str) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"x-request-id", request_id.encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """纯 ASGI 中间件：累计请求体字节，超限在前置阶段返回 413。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = _limit_for(scope.get("path", ""))
        if limit is None:
            await self.app(scope, receive, send)
            return

        buffered: list[dict[str, Any]] = []
        total = 0
        request_id = _request_id_from_scope(scope)
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                buffered.append(message)
                continue
            total += len(message.get("body", b""))
            if total > limit:
                # 不进入应用层，保证在解析与任务创建之前返回 413。
                await _send_json(send, *_payload(scope.get("path", "")), request_id=request_id)
                return
            buffered.append(message)
            if not message.get("more_body"):
                break

        async def replay() -> dict[str, Any]:
            if buffered:
                return buffered.pop(0)
            # 缓冲回放完毕后桥接底层 receive，等待真实的客户端断开。
            # 不能在此处合成 http.disconnect：EventSourceResponse 的
            # _listen_for_disconnect 收到后立即取消整个 task group，流式
            # 响应会在首帧前被切断，得到 200 + 空 body。
            return await receive()

        await self.app(scope, replay, send)
