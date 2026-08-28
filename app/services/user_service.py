"""用户用例：创建管理员、认证、改密与禁用编排。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..core.security import (
    hash_password,
    password_needs_rehash,
    verify_password,
)
from ..core.time import utc_now
from ..domain.enums import AuditAction, AuditResult, UserRole
from ..domain.errors import DomainError, DomainErrorCode
from ..domain.values import normalize_password, normalize_username, password_problems
from ..repositories.models import User
from ..repositories.system import AuditRepository
from ..repositories.users import UserRepository


class UserService:
    def __init__(self) -> None:
        self.users = UserRepository()
        self.audit = AuditRepository()

    def validate_credentials(self, username: str, password: str) -> list[str]:
        problems: list[str] = []
        if normalize_username(username) is None:
            problems.append("用户名仅允许 ASCII 字母数字与 . _ -（3-64 字符）")
        problems.extend(password_problems(password, username))
        return problems

    def create_admin(
        self,
        session: Session,
        *,
        username: str,
        display_name: str,
        password: str,
        must_change_password: bool = False,
        actor_user_id: int | None = None,
    ) -> User:
        normalized = normalize_username(username)
        if normalized is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "用户名格式不合法", status_code=400
            )
        problems = password_problems(password, normalized)
        if problems:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "; ".join(problems), status_code=400
            )
        if self.users.get_by_username(session, normalized) is not None:
            raise DomainError(DomainErrorCode.CONFLICT, "用户名已存在", status_code=409)
        user = self.users.create(
            session,
            username=normalized,
            display_name=display_name,
            password_hash=hash_password(normalize_password(password)),
            role=UserRole.ADMIN,
            must_change_password=must_change_password,
        )
        session.flush()
        self.audit.add(
            session,
            action=AuditAction.ADMIN_CREATED,
            resource_type="user",
            resource_id=str(user.id),
            actor_user_id=actor_user_id,
            owner_user_id=user.id,
        )
        return user

    def authenticate(self, session: Session, username: str, password: str) -> User | None:
        normalized = normalize_username(username)
        if normalized is None:
            return None
        user = self.users.get_by_username(session, normalized)
        if user is None or not user.is_active:
            return None
        normalized_password = normalize_password(password)
        if not verify_password(normalized_password, user.password_hash):
            return None
        # 参数低于当前策略时同流程重哈希。
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(normalized_password)
            session.flush()
        user.last_login_at = utc_now()
        return user

    def change_password(self, session: Session, user: User, new_password: str) -> None:
        problems = password_problems(new_password, user.username)
        if problems:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "; ".join(problems), status_code=400
            )
        user.password_hash = hash_password(normalize_password(new_password))
        user.must_change_password = False
        session.flush()

    def reset_password(self, session: Session, user: User, temp_password: str) -> None:
        problems = password_problems(temp_password, user.username)
        if problems:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "; ".join(problems), status_code=400
            )
        user.password_hash = hash_password(normalize_password(temp_password))
        user.must_change_password = True
        session.flush()

    def disable_user(self, session: Session, user: User, *, actor_user_id: int) -> None:
        """禁用编排：停用用户、撤销会话与 Key、终止活动任务并释放名额。

        M3 会接入会话/Key/任务的完整撤销；M2 先落地用户停用与审计骨架。
        """
        if user.role is UserRole.ADMIN and self.users.count_active_admins(session) <= 1:
            raise DomainError(
                DomainErrorCode.CONFLICT, "不能禁用最后一个有效管理员", status_code=409
            )
        self.users.set_active(session, user.id, False, disabled_by_user_id=actor_user_id)
        self.audit.add(
            session,
            action=AuditAction.USER_DISABLED,
            resource_type="user",
            resource_id=str(user.id),
            actor_user_id=actor_user_id,
            owner_user_id=user.id,
            result=AuditResult.SUCCESS,
        )
