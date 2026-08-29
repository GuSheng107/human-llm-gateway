"""用户级并发准入：先准入、再创建任务。

每个用户固定最多 10 个活动任务，所有 Key 与策略共用（docs/DATABASE.md
§10.2）；占位成功但后续持久化失败时同一事务回滚，不遗留名额。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..core.constants import MAX_ACTIVE_TASKS_PER_USER
from ..core.db import begin_immediate_if_sqlite
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.models import ApiKey, User
from ..repositories.users import UserRepository


class AdmissionService:
    def __init__(self) -> None:
        self.users = UserRepository()

    def acquire_slot(self, session: Session, key: ApiKey, owner: User) -> None:
        """原子占用用户活动任务名额；超过上限返回协议兼容 429。"""
        if not key.is_enabled or key.deleted_at is not None:
            raise DomainError(DomainErrorCode.INVALID_API_KEY, "API Key 无效", status_code=401)
        # 在任何读取前取得写锁，避免事务升级竞争（先锁后读）。
        begin_immediate_if_sqlite(session)
        session.refresh(owner)
        if not owner.is_active:
            raise DomainError(DomainErrorCode.INVALID_API_KEY, "API Key 无效", status_code=401)
        if not self.users.atomic_acquire_slot(session, owner.id):
            # 与禁用用户竞争时按 401 处理，其余情况才是真实的并发上限。
            session.refresh(owner)
            if not owner.is_active:
                raise DomainError(DomainErrorCode.INVALID_API_KEY, "API Key 无效", status_code=401)
            raise DomainError(
                DomainErrorCode.RATE_LIMIT_EXCEEDED,
                f"每个用户最多同时存在 {MAX_ACTIVE_TASKS_PER_USER} 个活动任务",
                status_code=429,
            )

    def release_slot(self, session: Session, user_id: int) -> None:
        """幂等释放名额（仅当名额仍被占用）。"""
        self.users.atomic_release_slot(session, user_id)
