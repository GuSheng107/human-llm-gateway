"""任务仓库原子事务测试：名额、首个回复、fallback、断开取消。"""

from __future__ import annotations

import json
import secrets

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.core.time import utc_now
from app.domain.enums import (
    DeliveryMode,
    InferenceProtocol,
    ReplyStrategy,
    TaskState,
)
from app.repositories import models  # noqa: F401
from app.repositories.models import ApiKey, RequestTask, User
from app.repositories.tasks import TaskRepository
from app.repositories.users import UserRepository


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


def _make_user(session) -> User:
    user = User(
        username="alice",
        display_name="Alice",
        password_hash="$argon2id$dummy",
        active_task_count=0,
    )
    session.add(user)
    session.flush()
    return user


def _make_key(session, user: User) -> ApiKey:
    key = ApiKey(
        owner_user_id=user.id,
        name="key",
        key_hash="hash",
        key_prefix="prefix",
        delivery_mode=DeliveryMode.WEB,
        reply_strategy=ReplyStrategy.HUMAN,
        human_timeout_seconds=300,
    )
    session.add(key)
    session.flush()
    return key


def _make_task(session, user: User, key: ApiKey, state: TaskState) -> RequestTask:
    task = RequestTask(
        public_id=f"task_{secrets.token_hex(8)}",
        owner_user_id=user.id,
        api_key_id=key.id,
        api_key_prefix_snapshot="prefix",
        requested_model="human-gateway",
        protocol=InferenceProtocol.OPENAI_CHAT,
        raw_payload_json="{}",
        normalized_request_json="{}",
        stream_requested=False,
        reply_strategy_snapshot=ReplyStrategy.HUMAN,
        delivery_mode_snapshot=DeliveryMode.WEB,
        state=state,
        slot_acquired_at=utc_now(),
    )
    session.add(task)
    session.flush()
    return task


class TestSlotAccounting:
    def test_acquire_up_to_limit_then_reject(self, session_factory) -> None:
        session = session_factory()
        user = _make_user(session)
        repo = UserRepository()
        for _ in range(10):
            assert repo.atomic_acquire_slot(session, user.id) is True
        assert repo.atomic_acquire_slot(session, user.id) is False
        session.rollback()

    def test_release_is_idempotent(self, session_factory) -> None:
        session = session_factory()
        user = _make_user(session)
        repo = UserRepository()
        assert repo.atomic_acquire_slot(session, user.id) is True
        session.flush()
        assert repo.atomic_release_slot(session, user.id) is True
        assert repo.atomic_release_slot(session, user.id) is False  # 已为 0 不再扣
        session.rollback()


class TestFirstReplyWins:
    def test_only_first_reply_succeeds(self, session_factory) -> None:
        session = session_factory()
        user = _make_user(session)
        key = _make_key(session, user)
        task = _make_task(session, user, key, TaskState.WAITING_HUMAN)
        repo = TaskRepository()

        ok = repo.first_reply_wins(
            session,
            task_id=task.id,
            owner_user_id=user.id,
            expected_version=1,
            response_payload_json=json.dumps({"final_text": "hi"}),
        )
        assert ok is True
        session.flush()

        second = repo.first_reply_wins(
            session,
            task_id=task.id,
            owner_user_id=user.id,
            expected_version=1,
            response_payload_json=json.dumps({"final_text": "late"}),
        )
        assert second is False
        session.rollback()


class TestTerminalAndCancel:
    def test_release_to_terminal_idempotent(self, session_factory) -> None:
        session = session_factory()
        user = _make_user(session)
        key = _make_key(session, user)
        task = _make_task(session, user, key, TaskState.RESPONDING)
        repo = TaskRepository()

        assert repo.release_slot_to_terminal(session, task.id, TaskState.COMPLETED) is True
        session.refresh(task)
        assert task.version == 2
        assert repo.release_slot_to_terminal(session, task.id, TaskState.COMPLETED) is False
        session.rollback()

    def test_cancel_caller_disconnected(self, session_factory) -> None:
        session = session_factory()
        user = _make_user(session)
        key = _make_key(session, user)
        task = _make_task(session, user, key, TaskState.WAITING_HUMAN)
        repo = TaskRepository()

        assert repo.cancel_caller_disconnected(session, task.id) is True
        session.refresh(task)
        assert task.version == 2
        # 已在终态，再次断开不再生效
        assert repo.cancel_caller_disconnected(session, task.id) is False
        session.rollback()

    def test_claim_fallback_is_exclusive(self, session_factory) -> None:
        session = session_factory()
        user = _make_user(session)
        key = _make_key(session, user)
        task = _make_task(session, user, key, TaskState.WAITING_HUMAN)
        repo = TaskRepository()

        assert repo.claim_fallback(session, task.id) is True
        session.flush()
        # 已不在 waiting_human，第二个执行者无法声明
        assert repo.claim_fallback(session, task.id) is False
        session.rollback()
