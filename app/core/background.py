"""后台线程的关闭边界：数据库工作结束后才允许协程退出。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


async def run_blocking_to_completion[T](function: Callable[[], T]) -> T:
    worker = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancelled:
        # 取消 asyncio Future 不会中断线程；必须等其释放数据库连接。
        try:
            await worker
        finally:
            raise cancelled
