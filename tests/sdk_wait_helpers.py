"""SDK 契约测试共用的人工回复等待辅助。

固定 ``sleep`` 等"任务已落库"在全量运行时必然漏：漏掉时后台回复协程会
静默死亡（异常没有任何人 await），而人工回复端点本就没有超时，HTTP 请求
便永久等待，整个 pytest 进程随之僵死。这里统一改成：

- 轮询等待目标 Key 出现**新**任务（而不是事后取"最新一条"，避免复用上一
  个用例已回复的任务）；
- 调用侧加兜底超时，让偶发时序问题表现为清晰失败而不是挂死；
- 收尾时显式抛出后台协程的异常，不再让失败消失。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import app.core.db as database
from app.domain.values import ReplyDraft
from app.repositories.models import RequestTask
from app.services.inference_service import InferenceService

# 任务落库只需毫秒级；10 秒是留给负载抖动的余量，超时即显式失败。
WAIT_TIMEOUT_SECONDS = 10.0
WAIT_INTERVAL_SECONDS = 0.05
# 人工回复路径没有端点超时，测试必须自带兜底，避免整个会话挂死。
SDK_CALL_TIMEOUT_SECONDS = 30.0


def latest_task_id(api_key_id: int) -> int | None:
    """该 Key 当前最新的任务 ID；没有任何任务时返回 None。"""
    with database.SessionLocal() as session:
        row = (
            session.query(RequestTask)
            .filter(RequestTask.api_key_id == api_key_id)
            .order_by(RequestTask.id.desc())
            .first()
        )
    return None if row is None else row.id


def task_state(task_id: int) -> Any:
    """任务当前状态；行不存在（尚未提交或已回滚）时返回 None。仅用于超时诊断。"""
    with database.SessionLocal() as session:
        row = session.get(RequestTask, task_id)
    return None if row is None else row.state


async def wait_for_new_task(
    api_key_id: int,
    *,
    after_id: int | None = None,
    timeout: float = WAIT_TIMEOUT_SECONDS,
) -> int:
    """轮询等待该 Key 出现 id 大于 ``after_id`` 的任务，返回其 ID。

    ``after_id`` 为 None 时以调用前的当前最大 ID 为基线：调用方应在发起请求
    **之前**取好基线，或显式传 0 表示任意任务。
    """
    baseline = latest_task_id(api_key_id) if after_id is None else after_id
    if baseline is None:
        baseline = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = latest_task_id(api_key_id)
        if current is not None and current > baseline:
            return current
        await asyncio.sleep(WAIT_INTERVAL_SECONDS)
    raise AssertionError(
        f"api_key_id={api_key_id} 在 {timeout}s 内没有创建新任务（基线 id={baseline}）"
    )


async def wait_for_task_state(
    api_key_id: int,
    state: Any,
    *,
    after_id: int | None = None,
    timeout: float = WAIT_TIMEOUT_SECONDS,
) -> int:
    """轮询等待该 Key 的新任务进入指定状态（如 WAITING_HUMAN），返回其 ID。"""
    task_id = await wait_for_new_task(api_key_id, after_id=after_id, timeout=timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with database.SessionLocal() as session:
            row = session.get(RequestTask, task_id)
        if row is not None and row.state is state:
            return task_id
        await asyncio.sleep(WAIT_INTERVAL_SECONDS)
    raise AssertionError(f"task_id={task_id} 在 {timeout}s 内没有进入状态 {state}")


def try_submit_reply(
    task_id: int, owner_user_id: int, *, reasoning: str | None = None, final_text: str
) -> bool:
    """尝试提交一次人工回复，返回是否被接受（与端点提交同一裁决路径）。

    任务行可能尚未提交、正在提交中或随后回滚，因此 task 缺失不是错误而是
    「还没就绪」，返回 False 交给调用方重试。
    """
    draft = ReplyDraft(reasoning=reasoning, final_text=final_text)
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        if task is None:
            return False
        accepted = InferenceService().tasks.first_reply_wins(
            session,
            task_id=task_id,
            owner_user_id=owner_user_id,
            expected_version=task.version,
            response_payload_json=draft.model_dump_json(exclude_none=True),
        )
        session.commit()
    return bool(accepted)


async def reply_when_task_ready(
    api_key_id: int, owner_user_id: int, *, reasoning: str | None, final_text: str, after_id: int
) -> None:
    """反复尝试提交，直到该 Key 的新任务真正接受回复。

    固定 sleep 会漏；只等「任务行出现」同样不够——任务可能尚未提交、状态尚未
    推进到 waiting_human，甚至随后回滚。而 first_reply_wins 只在 waiting_human
    且版本匹配时才返回 True，用它作为就绪信号不需要任何时序假设。
    """
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    observed = "没有出现新任务"
    while time.monotonic() < deadline:
        task_id = latest_task_id(api_key_id)
        if task_id is not None and task_id > after_id:
            observed = f"task_id={task_id} state={task_state(task_id)}"
            if try_submit_reply(task_id, owner_user_id, reasoning=reasoning, final_text=final_text):
                return
        await asyncio.sleep(WAIT_INTERVAL_SECONDS)
    raise AssertionError(
        f"api_key_id={api_key_id} 的新任务在 {WAIT_TIMEOUT_SECONDS}s 内没有接受回复"
        f"（基线 id={after_id}，最后一次观察：{observed}）"
    )


def start_reply_task(
    api_key_id: int,
    owner_user_id: int,
    *,
    reasoning: str | None = None,
    final_text: str,
    after_id: int,
) -> asyncio.Task[None]:
    """发起后台人工回复；配合 :func:`finish_reply_task` 使用。"""
    return asyncio.create_task(
        reply_when_task_ready(
            api_key_id,
            owner_user_id,
            reasoning=reasoning,
            final_text=final_text,
            after_id=after_id,
        )
    )


async def finish_reply_task(task: asyncio.Task[None]) -> None:
    """收尾后台回复协程：未取消且有异常时显式抛出，绝不静默吞掉。"""
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        raise exception
