"""用户仓库：所有权查询与并发名额原子更新。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core.time import utc_now
from ..domain.enums import UserRole
from .models import User


def _now() -> datetime:
    return utc_now()


class UserRepository:
    def get_by_id(self, session: Session, user_id: int) -> User | None:
        return session.get(User, user_id)

    def get_by_username(self, session: Session, username: str) -> User | None:
        return session.execute(select(User).where(User.username == username)).scalar_one_or_none()

    def create(
        self,
        session: Session,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        role: UserRole = UserRole.USER,
        must_change_password: bool = False,
        registered_via_invitation_id: int | None = None,
    ) -> User:
        user = User(
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            role=role,
            must_change_password=must_change_password,
            registered_via_invitation_id=registered_via_invitation_id,
        )
        session.add(user)
        return user

    def count_active_admins(self, session: Session) -> int:
        return (
            session.query(User)
            .filter(User.role == UserRole.ADMIN, User.is_active.is_(True))
            .count()
        )

    def atomic_acquire_slot(self, session: Session, user_id: int) -> bool:
        """用户活动任务名额 +1；返回是否成功（已达 10 或用户停用则失败）。"""
        result = session.execute(
            update(User)
            .where(
                User.id == user_id,
                User.is_active.is_(True),
                User.active_task_count < 10,
            )
            .values(active_task_count=User.active_task_count + 1, updated_at=_now())
        )
        return result.rowcount == 1

    def atomic_release_slot(self, session: Session, user_id: int) -> bool:
        """用户活动任务名额 -1；已为 0 时幂等不扣。"""
        result = session.execute(
            update(User)
            .where(User.id == user_id, User.active_task_count > 0)
            .values(active_task_count=User.active_task_count - 1, updated_at=_now())
        )
        return result.rowcount == 1

    def set_active(
        self,
        session: Session,
        user_id: int,
        is_active: bool,
        *,
        disabled_by_user_id: int | None = None,
    ) -> int:
        values: dict = {"is_active": is_active, "updated_at": _now()}
        if not is_active:
            values["disabled_at"] = _now()
            values["disabled_by_user_id"] = disabled_by_user_id
        else:
            values["disabled_at"] = None
            values["disabled_by_user_id"] = None
        result = session.execute(update(User).where(User.id == user_id).values(**values))
        return result.rowcount
