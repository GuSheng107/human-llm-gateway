"""自动转发的工作线程生命周期；取消会关闭线程内的异步上游请求。"""

from __future__ import annotations

import asyncio
from threading import Event

from anyio import CancelScope
from starlette.concurrency import run_in_threadpool

from ..domain.tasks import TERMINAL_STATES
from ..repositories.models import RequestTask
from .llm_forward_service import LlmForwardService

_CANCEL_POLL_SECONDS = 0.1


async def run_forward(task_id: int, *, reason: str, stream: bool) -> tuple[bool, str | None]:
    """同步数据库始终留在工作线程；返回前等待上游关闭和 Session 清理。"""
    stop = Event()
    worker = asyncio.create_task(run_in_threadpool(_run, task_id, reason, stream, stop))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        stop.set()
        # ASGI 取消可能来自持续生效的 AnyIO cancel scope，清理须屏蔽它。
        with CancelScope(shield=True):
            cleanup = asyncio.gather(worker, return_exceptions=True)
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    # 重复传输层取消不能丢下仍持有 HTTP 连接的工作线程。
                    continue
        raise


def _run(task_id: int, reason: str, stream: bool, stop: Event) -> tuple[bool, str | None]:
    return asyncio.run(_execute(task_id, reason, stream, stop))


async def _execute(task_id: int, reason: str, stream: bool, stop: Event) -> tuple[bool, str | None]:
    from ..core.db import SessionLocal

    with SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        if task is None:
            return False, "task_missing"
        service = LlmForwardService()
        method = service.forward_stream if stream else service.forward
        operation = asyncio.create_task(method(session, task, reason=reason))
        try:
            while not operation.done():
                await asyncio.wait({operation}, timeout=_CANCEL_POLL_SECONDS)
                if operation.done():
                    break
                # 数据库终态是事实来源：禁用用户、关闭或外部断开都终止上游。
                with SessionLocal() as current_session:
                    current = current_session.get(RequestTask, task_id)
                    ended = current is None or current.state in TERMINAL_STATES
                if stop.is_set() or ended:
                    operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)
                    return False, "cancelled"
            accepted, _result, error = await operation
            return accepted, error
        finally:
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
