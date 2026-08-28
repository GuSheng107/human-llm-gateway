"""用户仓库：所有权查询与并发名额原子更新。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..core.time import utc_now
from ..domain.enums import UserRole
from .models import (
    ApiKey,
    AssistantSession,
    FakeModel,
    ImConnection,
    LlmConfig,
    ModelGroup,
    RequestTask,
    User,
)


def _now() -> datetime:
    return utc_now()


class UserRepository:
    def get_by_id(self, session: Session, user_id: int) -> User | None:
        return session.get(User, user_id)

    def get_by_username(self, session: Session, username: str) -> User | None:
        return session.execute(select(User).where(User.username == username)).scalar_one_or_none()

    def get_by_email(self, session: Session, email: str) -> User | None:
        return session.execute(select(User).where(User.email == email)).scalar_one_or_none()

    def list_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[User], int]:
        filters = []
        if search:
            term = search.strip()
            filters.append(
                or_(User.username.ilike(f"%{term}%"), User.display_name.ilike(f"%{term}%"))
            )
        if is_active is not None:
            filters.append(User.is_active.is_(is_active))
        total = session.scalar(select(func.count()).select_from(User).where(*filters)) or 0
        rows = list(
            session.scalars(
                select(User)
                .where(*filters)
                .order_by(User.created_at.desc(), User.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

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
        email: str | None = None,
    ) -> User:
        user = User(
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            role=role,
            must_change_password=must_change_password,
            registered_via_invitation_id=registered_via_invitation_id,
            email=email,
        )
        session.add(user)
        return user

    def lock_active_admin_ids(self, session: Session) -> list[int]:
        """锁定有效管理员集合，防止并发请求同时禁用最后两个管理员。"""
        return list(
            session.scalars(
                select(User.id)
                .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
                .order_by(User.id)
                .with_for_update()
            )
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

    def clear_active_task_count(self, session: Session, user_id: int) -> int:
        result = session.execute(
            update(User).where(User.id == user_id).values(active_task_count=0, updated_at=_now())
        )
        return result.rowcount

    def resource_counts(self, session: Session, user_id: int) -> dict[str, int]:
        def count(model, *filters) -> int:
            return session.scalar(select(func.count()).select_from(model).where(*filters)) or 0

        return {
            "im_connections": count(
                ImConnection,
                ImConnection.owner_user_id == user_id,
                ImConnection.deleted_at.is_(None),
            ),
            "llm_configs": count(
                LlmConfig,
                LlmConfig.owner_user_id == user_id,
                LlmConfig.deleted_at.is_(None),
            ),
            "fake_models": count(
                FakeModel,
                FakeModel.owner_user_id == user_id,
                FakeModel.deleted_at.is_(None),
            ),
            "model_groups": count(
                ModelGroup,
                ModelGroup.owner_user_id == user_id,
                ModelGroup.deleted_at.is_(None),
            ),
            "api_keys": count(
                ApiKey,
                ApiKey.owner_user_id == user_id,
                ApiKey.deleted_at.is_(None),
            ),
            "tasks": count(RequestTask, RequestTask.owner_user_id == user_id),
            "assistant_sessions": count(
                AssistantSession,
                AssistantSession.owner_user_id == user_id,
                AssistantSession.deleted_at.is_(None),
            ),
        }
