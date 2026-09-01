"""僵尸任务收敛：超时终态化与陈旧输出态取消。

原始调用方的等待循环是超时终态与断开取消的唯一驱动；进程重启或断开
未被检测时循环消失，任务会永远停在等待/转发/输出态并占用活动名额
（上限 10 个，会被卡满）。本服务周期性扫描收敛两类残留：

- WAITING_HUMAN / FORWARDING_LLM 且人工截止已过 -> TIMED_OUT；
  与等待循环超时路径同一语义（allowed_sources 防人工先到覆盖）。
- RESPONSE_READY / RESPONDING 长时间无推进 -> CANCELLED
  （结果已落库但调用方已消失，宽限期后按断开取消释放名额）。

所有推进复用仓库层条件 UPDATE（rowcount 裁决），多实例并发扫描天然
幂等安全；单轮失败不影响后续轮次。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.logging import log_event
from ..core.time import utc_now
from ..domain.enums import TaskState
from ..repositories.models import RequestTask
from .inference_service import InferenceService

logger = logging.getLogger(__name__)

# 扫描周期（秒）
SWEEP_INTERVAL_SECONDS = 15.0
# 输出态（response_ready / responding）无推进视为调用方已消失的宽限（秒）。
STALE_OUTPUT_GRACE_SECONDS = 600
# 单轮单路径最大处理数（防御长事务）。
_SWEEP_BATCH_LIMIT = 200


class TaskSweeper:
    def __init__(self) -> None:
        self.service = InferenceService()

    def sweep_once(self, session: Session) -> dict[str, int]:
        """执行一轮收敛并提交；返回各路径处理计数（用于日志与测试）。"""
        now = utc_now()
        timed_out = 0
        cancelled = 0

        overdue = list(
            session.scalars(
                select(RequestTask)
                .where(
                    RequestTask.state.in_([TaskState.WAITING_HUMAN, TaskState.FORWARDING_LLM]),
                    RequestTask.human_deadline_at.is_not(None),
                    RequestTask.human_deadline_at <= now,
                    RequestTask.slot_released_at.is_(None),
                )
                .order_by(RequestTask.id.asc())
                .limit(_SWEEP_BATCH_LIMIT)
            )
        )
        for task in overdue:
            # allowed_sources 用读取到的源状态：人工/转发恰在扫描间隙先到时，
            # 条件 UPDATE 不命中，晚到的超时不覆盖。
            if self.service.finalize(
                session, task, TaskState.TIMED_OUT, allowed_sources={task.state}
            ):
                timed_out += 1

        stale_cutoff = now - timedelta(seconds=STALE_OUTPUT_GRACE_SECONDS)
        stale = list(
            session.scalars(
                select(RequestTask)
                .where(
                    RequestTask.state.in_([TaskState.RESPONSE_READY, TaskState.RESPONDING]),
                    RequestTask.updated_at.is_not(None),
                    RequestTask.updated_at <= stale_cutoff,
                    RequestTask.slot_released_at.is_(None),
                )
                .order_by(RequestTask.id.asc())
                .limit(_SWEEP_BATCH_LIMIT)
            )
        )
        for task in stale:
            if self.service.cancel_caller_disconnected(
                session, task.id, reason="stale_output_swept"
            ):
                cancelled += 1

        session.commit()
        return {"timed_out": timed_out, "cancelled": cancelled}

    def _sweep(self) -> dict[str, int]:
        from ..core.db import SessionLocal
        from ..core.logging import log_event

        with SessionLocal() as session:
            counts = self.sweep_once(session)
        if counts["timed_out"] or counts["cancelled"]:
            log_event("info", "task_sweeper.converged", "僵尸任务收敛", **counts)
        return counts

    async def run(self) -> None:
        """应用级周期循环；每轮独立失败，不终止后续扫描。"""
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            try:
                await asyncio.get_running_loop().run_in_executor(None, self._sweep)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("task sweeper cycle failed")
                log_event("error", "task_sweeper.cycle_failed", "僵尸任务扫描周期失败")


task_sweeper = TaskSweeper()
