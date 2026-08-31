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
    normalize_avatar_base64,
    normalize_display_name,
    normalize_email,
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
        email: str | None = None,
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
            email=email,
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
        email: str | None = None,
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
        normalized_email = self._validate_email(session, email)
        user = self.users.create(
            session,
            username=normalized,
            display_name=normalized_display_name,
            password_hash=hash_password(normalize_password(password)),
            role=role,
            must_change_password=must_change_password,
            registered_via_invitation_id=registered_via_invitation_id,
            email=normalized_email,
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

    @staticmethod
    def _validate_email(session: Session, email: str | None) -> str | None:
        normalized = normalize_email(email)
        if email is not None and normalized is None:
            raise DomainError(DomainErrorCode.VALIDATION_FAILED, "邮箱格式不合法", status_code=400)
        if (
            normalized is not None
            and UserRepository().get_by_email(session, normalized) is not None
        ):
            raise DomainError(DomainErrorCode.CONFLICT, "邮箱已被使用", status_code=409)
        return normalized

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

    def update_profile(
        self,
        session: Session,
        user: User,
        *,
        display_name: str,
        email: str | None,
        avatar_base64: str | None,
        actor_user_id: int,
    ) -> User:
        """更新显示名、邮箱与头像；未提交字段由调用方以 None 表示，空串用于清空。"""
        normalized = normalize_display_name(display_name)
        if normalized is None:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                "显示名不能为空且最多 100 个字符",
                status_code=400,
            )
        changed: list[str] = []
        user.display_name = normalized
        changed.append("display_name")
        if email is not None:
            normalized_email = normalize_email(email)
            if email.strip() and normalized_email is None:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED, "邮箱格式不合法", status_code=400
                )
            existing = (
                self.users.get_by_email(session, normalized_email) if normalized_email else None
            )
            if existing is not None and existing.id != user.id:
                raise DomainError(DomainErrorCode.CONFLICT, "邮箱已被使用", status_code=409)
            user.email = normalized_email or None
            changed.append("email")
        if avatar_base64 is not None:
            normalized_avatar = normalize_avatar_base64(avatar_base64)
            if normalized_avatar is None:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    "头像需为 PNG/JPEG 且不超过 256KB",
                    status_code=400,
                )
            user.avatar_base64 = normalized_avatar or None
            changed.append("avatar_base64")
        self.audit.add(
            session,
            action=AuditAction.USER_UPDATED,
            resource_type="user",
            resource_id=str(user.id),
            actor_user_id=actor_user_id,
            owner_user_id=user.id,
            metadata={"fields": changed},
        )
        session.flush()
        return user

    def update_display_name(
        self, session: Session, user: User, display_name: str, *, actor_user_id: int
    ) -> User:
        return self.update_profile(
            session,
            user,
            display_name=display_name,
            email=None,
            avatar_base64=None,
            actor_user_id=actor_user_id,
        )

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

    def set_password(
        self,
        session: Session,
        user: User,
        *,
        new_password: str,
        keep_token_hash: str,
    ) -> None:
        """强制改密：不校验旧密码，设置新哈希并撤销其余会话。"""
        problems = password_problems(new_password, user.username)
        if problems:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "; ".join(problems), status_code=400
            )
        user.password_hash = hash_password(normalize_password(new_password))
        user.must_change_password = False
        self.sessions.revoke_all_except(session, user.id, keep_token_hash)
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
        if user.id == actor_user_id:
            raise DomainError(DomainErrorCode.FORBIDDEN, "不能禁用自己的账号", status_code=403)
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
