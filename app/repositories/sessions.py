"""会话仓库。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..core.time import utc_now
from .models import AuthSession


def _now() -> datetime:
    return utc_now()


class AuthSessionRepository:
    def create(
        self,
        session: Session,
        *,
        user_id: int,
        token_hash: str,
        token_prefix: str,
        expires_at: datetime,
    ) -> AuthSession:
        row = AuthSession(
            user_id=user_id,
            token_hash=token_hash,
            token_prefix=token_prefix,
            expires_at=expires_at,
        )
        session.add(row)
        return row

    def get_by_token_hash(self, session: Session, token_hash: str) -> AuthSession | None:
        return session.execute(
            select(AuthSession).where(AuthSession.token_hash == token_hash)
        ).scalar_one_or_none()

    def revoke(self, session: Session, session_id: int) -> int:
        result = session.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        return result.rowcount

    def revoke_all_except(self, session: Session, user_id: int, token_hash: str) -> int:
        """撤销除当前会话外的全部会话（强制改密后保留本次登录）。"""
        result = session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.token_hash != token_hash,
            )
            .values(revoked_at=_now())
        )
        return result.rowcount

    def revoke_all_for_user(self, session: Session, user_id: int) -> int:
        result = session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        return result.rowcount

    def count_active_for_user(self, session: Session, user_id: int) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(
                    AuthSession.user_id == user_id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > _now(),
                )
            )
            or 0
        )
