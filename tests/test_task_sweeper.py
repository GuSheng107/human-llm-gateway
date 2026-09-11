"""任务收敛器测试（task_sweeper）：超时终态化与陈旧输出态取消。

覆盖：
- 人工截止与上游总时长独立收敛，不抢占正常 fallback
- 未过截止的任务不受影响
- 陈旧 RESPONSE_READY -> CANCELLED（stale_output_swept）并释放名额
- 新鲜 RESPONSE_READY 不被误杀
- 幂等：二次扫描不重复处理
"""

from __future__ import annotations

import json
from datetime import timedelta

import app.core.db as database
from app.core.time import utc_now
from app.domain.enums import InferenceProtocol, ReplyStrategy, TaskState
from app.protocols import chat_completions as chat_protocol
from app.repositories.models import ApiKey, RequestTask, User
from app.services.inference_service import InferenceService
from app.services.task_sweeper import (
    FALLBACK_CLAIM_GRACE_SECONDS,
    STALE_FORWARD_GRACE_SECONDS,
    STALE_OUTPUT_GRACE_SECONDS,
    TaskSweeper,
)


def _make_waiting_task(key_id: int, owner_user_id: int, *, content: str = "hello") -> int:
    payload = {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": content}]}
    raw = json.dumps(payload).encode()
    parsed = chat_protocol.parse_request(raw)
    service = InferenceService()
    with database.SessionLocal() as session:
        key = session.get(ApiKey, key_id)
        owner = session.get(User, owner_user_id)
        task = service.create_task(
            session,
            key=key,
            owner=owner,
            protocol=InferenceProtocol.OPENAI_CHAT,
            parsed=parsed,
            raw_body=raw,
            headers={},
        )
        session.commit()
        assert task.id is not None
        return task.id


def _mutate(task_id: int, **fields) -> None:
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        assert task is not None
        for name, value in fields.items():
            setattr(task, name, value)
        session.commit()


def _load(task_id: int) -> RequestTask:
    with database.SessionLocal() as session:
        task = session.get(RequestTask, task_id)
        assert task is not None
        # 脱离会话后读取属性（expire_on_commit=False，属性已加载）。
        return task


def test_overdue_waiting_human_swept_to_timed_out(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    _mutate(task_id, human_deadline_at=utc_now() - timedelta(seconds=1))

    sweeper = TaskSweeper()
    with database.SessionLocal() as session:
        counts = sweeper.sweep_once(session)

    assert counts["timed_out"] == 1
    task = _load(task_id)
    assert task.state is TaskState.TIMED_OUT
    assert task.slot_released_at is not None
    assert task.public_error_code == "request_timeout"


def test_overdue_forwarding_llm_swept_to_timed_out(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    _mutate(
        task_id,
        state=TaskState.FORWARDING_LLM,
        human_deadline_at=utc_now() - timedelta(seconds=1),
        updated_at=utc_now() - timedelta(seconds=STALE_FORWARD_GRACE_SECONDS + 1),
    )

    with database.SessionLocal() as session:
        counts = TaskSweeper().sweep_once(session)

    assert counts["timed_out"] == 1
    assert _load(task_id).state is TaskState.TIMED_OUT


def test_fallback_claim_and_generation_have_independent_budgets(
    client, created_user, created_key
) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    _mutate(
        task_id,
        reply_strategy_snapshot=ReplyStrategy.HUMAN_FALLBACK_LLM,
        human_deadline_at=utc_now() - timedelta(seconds=1),
    )
    with database.SessionLocal() as session:
        assert TaskSweeper().sweep_once(session)["timed_out"] == 0
    from app.repositories.tasks import TaskRepository

    with database.SessionLocal() as session:
        assert TaskRepository().claim_fallback(session, task_id)
        session.commit()
        assert TaskSweeper().sweep_once(session)["timed_out"] == 0
    assert _load(task_id).state is TaskState.FORWARDING_LLM


def test_orphaned_fallback_waiter_eventually_releases_slot(
    client, created_user, created_key
) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    _mutate(
        task_id,
        reply_strategy_snapshot=ReplyStrategy.HUMAN_FALLBACK_LLM,
        human_deadline_at=utc_now() - timedelta(seconds=FALLBACK_CLAIM_GRACE_SECONDS + 1),
    )
    with database.SessionLocal() as session:
        assert TaskSweeper().sweep_once(session)["timed_out"] == 1
    assert _load(task_id).slot_released_at is not None


def test_pending_task_not_swept(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    # 截止仍在未来：不得被收敛。
    _mutate(task_id, human_deadline_at=utc_now() + timedelta(seconds=60))

    with database.SessionLocal() as session:
        counts = TaskSweeper().sweep_once(session)

    assert counts["timed_out"] == 0
    assert _load(task_id).state is TaskState.WAITING_HUMAN


def test_stale_response_ready_cancelled(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    draft = json.dumps({"reasoning": None, "tool_calls": [], "final_text": "ok"})
    _mutate(
        task_id,
        state=TaskState.RESPONSE_READY,
        response_payload_json=draft,
        updated_at=utc_now() - timedelta(seconds=STALE_OUTPUT_GRACE_SECONDS + 60),
    )

    with database.SessionLocal() as session:
        counts = TaskSweeper().sweep_once(session)

    assert counts["cancelled"] == 1
    task = _load(task_id)
    assert task.state is TaskState.CANCELLED
    assert task.cancel_reason_code == "stale_output_swept"
    assert task.slot_released_at is not None


def test_fresh_response_ready_not_cancelled(client, created_user, created_key) -> None:
    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    draft = json.dumps({"reasoning": None, "tool_calls": [], "final_text": "ok"})
    _mutate(
        task_id,
        state=TaskState.RESPONSE_READY,
        response_payload_json=draft,
        updated_at=utc_now() - timedelta(seconds=30),
    )

    with database.SessionLocal() as session:
        counts = TaskSweeper().sweep_once(session)

    assert counts["cancelled"] == 0
    assert _load(task_id).state is TaskState.RESPONSE_READY


def test_sweep_idempotent_and_releases_slot(client, created_user, created_key) -> None:
    from app.repositories.tasks import TaskRepository

    task_id = _make_waiting_task(created_key.id, created_user.user_id)
    _mutate(task_id, human_deadline_at=utc_now() - timedelta(seconds=1))

    with database.SessionLocal() as session:
        before = TaskRepository().count_active_for_user(session, created_user.user_id)
        first = TaskSweeper().sweep_once(session)
        second = TaskSweeper().sweep_once(session)
        after = TaskRepository().count_active_for_user(session, created_user.user_id)

    assert before == 1
    assert first["timed_out"] == 1
    assert second["timed_out"] == 0  # 已终态且名额已释放，二次扫描不命中
    assert after == 0
