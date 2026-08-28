"""认证用例：登录签发会话、登出、按 token 取当前用户。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.db import begin_immediate_if_sqlite
from ..core.security import generate_session_token, hash_session_token
from ..core.time import utc_now
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.models import User
from ..repositories.sessions import AuthSessionRepository
from .invitation_service import InvitationService
from .user_service import UserService

_SESSION_TTL = timedelta(hours=8)


class AuthService:
    def __init__(self) -> None:
        self.sessions = AuthSessionRepository()
        self.users = UserService()
        self.invitations = InvitationService()

    def register(
        self,
        session: Session,
        *,
        invitation_code: str,
        username: str,
        display_name: str,
        password: str,
    ) -> User:
        begin_immediate_if_sqlite(session)
        invitation = self.invitations.match_plaintext(session, invitation_code)
        if not self.invitations.invitations.atomic_consume(session, invitation.id):
            raise DomainError(DomainErrorCode.INVALID_INVITATION, "邀请码无效", status_code=400)
        try:
            user = self.users.create_user(
                session,
                username=username,
                display_name=display_name,
                password=password,
                must_change_password=False,
                registered_via_invitation_id=invitation.id,
            )
            session.commit()
            return user
        except IntegrityError as exc:
            session.rollback()
            raise DomainError(DomainErrorCode.CONFLICT, "用户名已存在", status_code=409) from exc

    def login(self, session: Session, username: str, password: str) -> tuple[str, datetime, User]:
        user = self.users.authenticate(session, username, password)
        if user is None:
            raise DomainError(DomainErrorCode.UNAUTHORIZED, "用户名或密码错误", status_code=401)
        token, prefix, token_hash = generate_session_token()
        expires_at = utc_now() + _SESSION_TTL
        self.sessions.create(
            session,
            user_id=user.id,
            token_hash=token_hash,
            token_prefix=prefix,
            expires_at=expires_at,
        )
        session.commit()
        return token, expires_at, user

    def logout(self, session: Session, token: str) -> None:
        row = self.sessions.get_by_token_hash(session, hash_session_token(token))
        if row is not None:
            self.sessions.revoke(session, row.id)
            session.commit()

    def get_user_by_token(self, session: Session, token: str) -> User | None:
        row = self.sessions.get_by_token_hash(session, hash_session_token(token))
        if row is None or row.revoked_at is not None:
            return None
        if row.expires_at < utc_now():
            return None
        user = session.get(User, row.user_id)
        if user is None or not user.is_active:
            return None
        return user
