"""M5 用户级并发准入：多 Key 多策略不能绕过 10 个活动任务上限。"""

from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.constants import MAX_ACTIVE_TASKS_PER_USER
from app.core.db import _make_engine
from app.core.security import encrypt_secret
from app.domain.enums import DeliveryMode, ReplyStrategy
from app.domain.errors import DomainError, DomainErrorCode
from app.repositories.models import ApiKey, LlmConfig, User
from app.services.admission import AdmissionService
from app.services.api_key_service import ApiKeyService
from app.services.bootstrap import BootstrapService
from app.services.user_service import UserService


def _prepare(sessions) -> tuple[int, list[int]]:
    """在临时文件库准备一个用户和三个不同策略的 Key。"""
    with sessions() as session:
        user = UserService().create_user(
            session,
            username="slot-user",
            display_name="Slot User",
            password="Slot-User-Pass1!",
            must_change_password=False,
        )
        session.commit()
        owner_id = user.id

    with sessions() as session:
        secret = encrypt_secret("sk-admission", get_settings().app_secret, "llm-secret")
        config = LlmConfig(
            owner_user_id=owner_id,
            name="upstream",
            protocol="openai_chat",
            base_url="https://example.test/v1",
            real_model="gpt-admission",
            secret_ciphertext=secret,
            encryption_key_version=1,
            timeout_seconds=60,
        )
        session.add(config)
        session.commit()
        config_id = config.id

    with sessions() as session:
        owner = session.get(User, owner_id)
        keys: list[int] = []
        for name, strategy in (
            ("human-key", ReplyStrategy.HUMAN),
            ("llm-key", ReplyStrategy.LLM),
            ("fallback-key", ReplyStrategy.HUMAN_FALLBACK_LLM),
        ):
            row, _plaintext = ApiKeyService().create(
                session,
                owner=owner,
                name=name,
                delivery_mode=DeliveryMode.WEB,
                reply_strategy=strategy,
                llm_config_id=None if strategy is ReplyStrategy.HUMAN else config_id,
            )
            keys.append(row.id)
        session.commit()
        return owner_id, keys


def test_slot_limit_is_shared_across_multiple_keys_and_strategies() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "admission.db"
        url = f"sqlite:///{path.as_posix()}"
        engine = _make_engine(url)
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        settings = Settings(
            database_url=url,
            app_secret=secrets.token_urlsafe(32),
            admin_username="admin",
            admin_password="Admission-Admin1!",
        )
        with sessions() as session:
            BootstrapService().initialize(session, settings)

        owner_id, key_ids = _prepare(sessions)

        def attempt(index: int) -> str:
            with sessions() as session:
                user = session.get(User, owner_id)
                key = session.get(ApiKey, key_ids[index % len(key_ids)])
                try:
                    AdmissionService().acquire_slot(session, key, user)
                except DomainError as exc:
                    session.rollback()
                    return exc.code.value
                session.commit()
                return "admitted"

        with ThreadPoolExecutor(max_workers=12) as executor:
            outcomes = list(executor.map(attempt, range(14)))

        assert outcomes.count("admitted") == MAX_ACTIVE_TASKS_PER_USER
        assert set(outcomes) - {"admitted"} == {DomainErrorCode.RATE_LIMIT_EXCEEDED.value}

        with sessions() as session:
            assert session.get(User, owner_id).active_task_count == MAX_ACTIVE_TASKS_PER_USER
            # 超额次数不写入计数，重复释放也不会把计数扣成负数。
            service = AdmissionService()
            for _ in range(MAX_ACTIVE_TASKS_PER_USER + 5):
                service.release_slot(session, owner_id)
            session.commit()
            assert session.get(User, owner_id).active_task_count == 0
            service.release_slot(session, owner_id)
            session.commit()
            assert session.get(User, owner_id).active_task_count == 0
        engine.dispose()


def test_disabled_key_or_user_cannot_acquire_slot() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "admission-blocked.db"
        url = f"sqlite:///{path.as_posix()}"
        engine = _make_engine(url)
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        settings = Settings(
            database_url=url,
            app_secret=secrets.token_urlsafe(32),
            admin_username="admin",
            admin_password="Admission-Admin2!",
        )
        with sessions() as session:
            BootstrapService().initialize(session, settings)

        owner_id, key_ids = _prepare(sessions)

        with sessions() as session:
            key = session.get(ApiKey, key_ids[0])
            key.is_enabled = False
            session.commit()

        with sessions() as session:
            user = session.get(User, owner_id)
            key = session.get(ApiKey, key_ids[0])
            try:
                AdmissionService().acquire_slot(session, key, user)
            except DomainError as exc:
                assert exc.code is DomainErrorCode.INVALID_API_KEY
                assert exc.status_code == 401
            else:  # pragma: no cover
                raise AssertionError("停用的 Key 不应通过准入")
            session.rollback()

        # 用户被禁用后，即使 Key 有效也不能准入。
        with sessions() as session:
            session.get(User, owner_id).is_active = False
            session.commit()
        with sessions() as session:
            user = session.get(User, owner_id)
            key = session.get(ApiKey, key_ids[1])
            try:
                AdmissionService().acquire_slot(session, key, user)
            except DomainError as exc:
                assert exc.code is DomainErrorCode.INVALID_API_KEY
                assert exc.status_code == 401
            else:  # pragma: no cover
                raise AssertionError("禁用用户不应通过准入")
            session.rollback()
            assert session.get(User, owner_id).active_task_count == 0
        engine.dispose()
