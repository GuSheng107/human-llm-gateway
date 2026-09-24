"""响应发送生命周期：只有实际发送结束才能完成任务并释放名额。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from anyio import CancelScope
from fastapi.responses import JSONResponse
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.concurrency import run_in_threadpool
from starlette.types import Message, Receive, Scope, Send

from ..domain.errors import DomainError, DomainErrorCode


class TaskJSONResponse(JSONResponse):
    def __init__(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str],
        begin: Callable[[], bool],
        complete: Callable[[], bool],
        cancel: Callable[[], None],
    ) -> None:
        super().__init__(payload, headers=headers)
        self.begin = begin
        self.complete = complete
        self.cancel = cancel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            if not await run_in_threadpool(self.begin):
                raise DomainError(
                    DomainErrorCode.UPSTREAM_ERROR,
                    "The server had an error while processing the request.",
                    status_code=500,
                )
            await super().__call__(scope, receive, send)
            await run_in_threadpool(self.complete)
        finally:
            with CancelScope(shield=True):
                await run_in_threadpool(self.cancel)


class TaskEventSourceResponse(EventSourceResponse):
    def __init__(
        self,
        content: AsyncIterator[ServerSentEvent],
        *,
        cancel: Callable[[], None],
        complete: Callable[[], bool],
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(content, headers=headers)
        self.cancel = cancel
        self.complete = complete
        self.content = content

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        sent_end = False

        async def send_and_record(message: Message) -> None:
            nonlocal sent_end
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                sent_end = True

        try:
            await super().__call__(scope, receive, send_and_record)
            if sent_end:
                await run_in_threadpool(self.complete)
        finally:
            # 断开发生在 ASGI send 时，取消可能没有送进内容生成器。
            with CancelScope(shield=True):
                close = getattr(self.content, "aclose", None)
                if close is not None:
                    await close()
                await run_in_threadpool(self.cancel)
