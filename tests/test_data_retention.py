"""高频数据七日保留策略。"""

from __future__ import annotations

from datetime import timedelta

from app.core.time import utc_now
from app.domain.enums import AssistantRole, AuditAction, AuditResult, UserRole
from app.repositories.models import (
    AppLog,
    AssistantMessage,
    AssistantSession,
    AuditLog,
    AuthSession,
    User,
)
from app.services.data_retention import DataRetentionService


def test_retention_removes_old_high_frequency_data_but_keeps_recent_and_valid_session(
    client,
) -> None:
    now = utc_now()
    old = now - timedelta(days=8)
    recent = now - timedelta(days=1)

    import app.core.db as database

    with database.SessionLocal() as session:
        user = User(
            username="retention-user",
            display_name="retention-user",
            password_hash="x",
            role=UserRole.USER,
        )
        session.add(user)
        session.flush()

        old_session = AssistantSession(
            owner_user_id=user.id,
            title="旧会话",
            created_at=old,
            updated_at=old,
            last_message_at=old,
        )
        recent_session = AssistantSession(
            owner_user_id=user.id,
            title="新会话",
            created_at=recent,
            updated_at=recent,
            last_message_at=recent,
        )
        session.add_all([old_session, recent_session])
        session.flush()
        old_session_id = old_session.id
        recent_session_id = recent_session.id

        session.add_all(
            [
                AssistantMessage(
                    session_id=old_session_id,
                    role=AssistantRole.USER,
                    content_json='{"text":"old"}',
                    created_at=old,
                ),
                AssistantMessage(
                    session_id=recent_session_id,
                    role=AssistantRole.USER,
                    content_json='{"text":"recent"}',
                    created_at=recent,
                ),
                AppLog(event="old", message="old", created_at=old),
                AppLog(event="recent", message="recent", created_at=recent),
                AuditLog(
                    action=AuditAction.USER_CREATED.value,
                    resource_type="user",
                    resource_id="retention-old",
                    result=AuditResult.SUCCESS,
                    created_at=old,
                ),
                AuditLog(
                    action=AuditAction.USER_CREATED.value,
                    resource_type="user",
                    resource_id="retention-recent",
                    result=AuditResult.SUCCESS,
                    created_at=recent,
                ),
                AuthSession(
                    user_id=user.id,
                    token_hash="old-token",
                    token_prefix="old",
                    expires_at=old,
                    revoked_at=old,
                    created_at=old,
                ),
                AuthSession(
                    user_id=user.id,
                    token_hash="active-token",
                    token_prefix="active",
                    expires_at=now + timedelta(days=1),
                    created_at=old,
                ),
            ]
        )
        session.commit()

    counts = DataRetentionService().cleanup_once(now=now)

    assert counts["app_logs"] == 1
    assert counts["audit_logs"] == 1
    assert counts["assistant_messages"] == 1
    assert counts["assistant_sessions"] == 1
    assert counts["auth_sessions"] == 1

    with database.SessionLocal() as session:
        assert session.get(AssistantSession, old_session_id) is None
        assert session.get(AssistantSession, recent_session_id) is not None
        assert session.query(AppLog).filter(AppLog.event.in_(["old", "recent"])).count() == 1
        assert session.query(AuditLog).filter(AuditLog.resource_id == "retention-old").count() == 0
        assert (
            session.query(AuditLog).filter(AuditLog.resource_id == "retention-recent").count() == 1
        )
        assert session.query(AuthSession).count() == 1
