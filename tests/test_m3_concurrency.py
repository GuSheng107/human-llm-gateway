"""M3 邀请码并发消费不能突破最大使用次数。"""

from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.db import _make_engine
from app.domain.errors import DomainError, DomainErrorCode
from app.repositories.models import InvitationCode, User
from app.services.auth_service import AuthService
from app.services.bootstrap import BootstrapService
from app.services.invitation_service import InvitationService


def test_invitation_concurrency_respects_max_uses() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "concurrency.db"
        url = f"sqlite:///{path.as_posix()}"
        engine = _make_engine(url)
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        settings = Settings(
            database_url=url,
            app_secret=secrets.token_urlsafe(32),
            admin_username="admin",
            admin_password="concurrency-admin-password",
        )
        with sessions() as session:
            BootstrapService().initialize(session, settings)
            admin = session.query(User).filter(User.username == "admin").one()
            invitation, plaintext = InvitationService().create(
                session,
                actor_user_id=admin.id,
                note="并发测试",
                expires_at=None,
                max_uses=1,
            )
            invitation_id = invitation.id
            session.commit()

        def register(index: int) -> str:
            with sessions() as session:
                try:
                    AuthService().register(
                        session,
                        invitation_code=plaintext,
                        username=f"user-{index}",
                        display_name=f"User {index}",
                        password=f"concurrent-user-password-{index}",
                    )
                    return "created"
                except DomainError as exc:
                    session.rollback()
                    return exc.code.value

        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(register, range(8)))

        assert outcomes.count("created") == 1
        assert outcomes.count(DomainErrorCode.INVALID_INVITATION.value) == 7
        with sessions() as session:
            invitation = session.get(InvitationCode, invitation_id)
            assert invitation is not None and invitation.used_count == 1
            assert session.query(User).filter(User.role == "user").count() == 1
        engine.dispose()
