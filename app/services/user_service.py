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
from ..domain.values import (
    normalize_display_name,
    normalize_password,
    normalize_username,
    password_problems,
)
from ..repositories.api_keys import ApiKeyRepository
from ..repositories.models import User
from ..repositories.sessions import AuthSessionRepository
from ..repositories.system import AuditRepository
from ..repositories.tasks import TaskRepository
from ..repositories.users import UserRepository


class UserService:
    def __init__(self) -> None:
        self.users = UserRepository()
        self.audit = AuditRepository()
        self.sessions = AuthSessionRepository()
        self.api_keys = ApiKeyRepository()
        self.tasks = TaskRepository()

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
        return self._create_user(
            session,
            username=username,
            display_name=display_name,
            password=password,
            role=UserRole.ADMIN,
            must_change_password=must_change_password,
            actor_user_id=actor_user_id,
            action=AuditAction.ADMIN_CREATED,
        )

    def create_user(
        self,
        session: Session,
        *,
        username: str,
        display_name: str,
        password: str,
        must_change_password: bool,
        actor_user_id: int | None = None,
        registered_via_invitation_id: int | None = None,
    ) -> User:
        return self._create_user(
            session,
            username=username,
            display_name=display_name,
            password=password,
            role=UserRole.USER,
            must_change_password=must_change_password,
            actor_user_id=actor_user_id,
            action=AuditAction.USER_CREATED,
            registered_via_invitation_id=registered_via_invitation_id,
        )

    def _create_user(
        self,
        session: Session,
        *,
        username: str,
        display_name: str,
        password: str,
        role: UserRole,
        must_change_password: bool,
        actor_user_id: int | None,
        action: AuditAction,
        registered_via_invitation_id: int | None = None,
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
        normalized_display_name = normalize_display_name(display_name)
        if normalized_display_name is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "显示名不能为空且最多 100 个字符",
                status_code=400,
            )
        user = self.users.create(
            session,
            username=normalized,
            display_name=normalized_display_name,
            password_hash=hash_password(normalize_password(password)),
            role=role,
            must_change_password=must_change_password,
            registered_via_invitation_id=registered_via_invitation_id,
        )
        session.flush()
        self.audit.add(
            session,
            action=action,
            resource_type="user",
            resource_id=str(user.id),
            actor_user_id=actor_user_id,
            owner_user_id=user.id,
            metadata={"fields": ["username", "display_name", "role"]},
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

    def update_display_name(
        self, session: Session, user: User, display_name: str, *, actor_user_id: int
    ) -> User:
        normalized = normalize_display_name(display_name)
        if normalized is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "显示名不能为空且最多 100 个字符",
                status_code=400,
            )
        user.display_name = normalized
        self.audit.add(
            session,
            action=AuditAction.USER_UPDATED,
            resource_type="user",
            resource_id=str(user.id),
            actor_user_id=actor_user_id,
            owner_user_id=user.id,
            metadata={"fields": ["display_name"]},
        )
        session.flush()
        return user

    def change_password(
        self,
        session: Session,
        user: User,
        *,
        current_password: str,
        new_password: str,
    ) -> None:
        if not verify_password(normalize_password(current_password), user.password_hash):
            raise DomainError(DomainErrorCode.VALIDATION_FAILED, "当前密码错误", status_code=400)
        problems = password_problems(new_password, user.username)
        if problems:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "; ".join(problems), status_code=400
            )
        user.password_hash = hash_password(normalize_password(new_password))
        user.must_change_password = False
        self.audit.add(
            session,
            action=AuditAction.USER_PASSWORD_CHANGED,
            resource_type="user",
            resource_id=str(user.id),
            actor_user_id=user.id,
            owner_user_id=user.id,
            metadata={"fields": ["password_hash", "must_change_password"]},
        )
        session.flush()

    def reset_password(
        self,
        session: Session,
        user: User,
        temp_password: str,
        *,
        actor_user_id: int,
    ) -> None:
        if user.role is UserRole.ADMIN:
            raise DomainError(
                DomainErrorCode.FORBIDDEN,
                "管理员密码只能由本人修改或通过部署级流程处理",
                status_code=403,
            )
        problems = password_problems(temp_password, user.username)
        if problems:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "; ".join(problems), status_code=400
            )
        user.password_hash = hash_password(normalize_password(temp_password))
        user.must_change_password = True
        self.sessions.revoke_all_for_user(session, user.id)
        self.audit.add(
            session,
            action=AuditAction.USER_PASSWORD_RESET,
            resource_type="user",
            resource_id=str(user.id),
            actor_user_id=actor_user_id,
            owner_user_id=user.id,
            metadata={"fields": ["password_hash", "must_change_password"]},
        )
        session.flush()

    def disable_user(self, session: Session, user: User, *, actor_user_id: int) -> dict[str, int]:
        """同一事务停用用户、撤销会话/Key、取消任务并清零名额。"""
        if not user.is_active:
            return self.impact_counts(session, user.id)
        if user.role is UserRole.ADMIN:
            active_admin_ids = self.users.lock_active_admin_ids(session)
            if len(active_admin_ids) <= 1:
                raise DomainError(
                    DomainErrorCode.CONFLICT,
                    "不能禁用最后一个有效管理员",
                    status_code=409,
                )
        impact = self.impact_counts(session, user.id)
        self.users.set_active(session, user.id, False, disabled_by_user_id=actor_user_id)
        self.sessions.revoke_all_for_user(session, user.id)
        self.api_keys.disable_all_for_user(session, user.id)
        self.tasks.cancel_all_for_user(session, user.id, "user_disabled")
        self.users.clear_active_task_count(session, user.id)
        self.audit.add(
            session,
            action=AuditAction.USER_DISABLED,
            resource_type="user",
            resource_id=str(user.id),
            actor_user_id=actor_user_id,
            owner_user_id=user.id,
            result=AuditResult.SUCCESS,
            metadata={"fields": ["is_active", "auth_sessions", "api_keys", "tasks"]},
        )
        session.flush()
        return impact

    def enable_user(self, session: Session, user: User, *, actor_user_id: int) -> User:
        if user.is_active:
            return user
        self.users.set_active(session, user.id, True)
        self.audit.add(
            session,
            action=AuditAction.USER_ENABLED,
            resource_type="user",
            resource_id=str(user.id),
            actor_user_id=actor_user_id,
            owner_user_id=user.id,
            metadata={"fields": ["is_active"]},
        )
        session.flush()
        session.refresh(user)
        return user

    def impact_counts(self, session: Session, user_id: int) -> dict[str, int]:
        return {
            "active_sessions": self.sessions.count_active_for_user(session, user_id),
            "enabled_api_keys": self.api_keys.count_enabled_for_user(session, user_id),
            "active_tasks": self.tasks.count_active_for_user(session, user_id),
        }
