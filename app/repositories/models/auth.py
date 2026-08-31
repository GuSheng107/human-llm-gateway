"""身份与访问：users / auth_sessions / invitation_codes。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db import Base
from ...domain.enums import UserRole
from .base import TimestampMixin, sa_enum, utc_now


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "active_task_count >= 0 AND active_task_count <= 10", name="ck_users_tasks"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    role: Mapped[UserRole] = mapped_column(sa_enum(UserRole), default=UserRole.USER, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    active_task_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    registered_via_invitation_id: Mapped[int | None] = mapped_column(
        ForeignKey("invitation_codes.id", ondelete="SET NULL"), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    avatar_base64: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class InvitationCode(TimestampMixin, Base):
    __tablename__ = "invitation_codes"
    __table_args__ = (
        CheckConstraint("max_uses > 0", name="ck_invitation_max_uses"),
        CheckConstraint(
            "used_count >= 0 AND used_count <= max_uses", name="ck_invitation_used_count"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    code_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# 索引（模块级声明，绑定表）
Index("ix_users_role_is_active", User.role, User.is_active)
Index(
    "ix_auth_sessions_user_revoked_expires",
    AuthSession.user_id,
    AuthSession.revoked_at,
    AuthSession.expires_at,
)
Index(
    "ix_invitation_codes_expires_revoked",
    InvitationCode.expires_at,
    InvitationCode.revoked_at,
)
Index("ix_invitation_codes_created_by", InvitationCode.created_by_user_id)
