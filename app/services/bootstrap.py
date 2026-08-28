"""应用初始化：建表、Schema 版本校验、sentinel、管理员与默认模型种子。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.constants import SCHEMA_VERSION
from ..core.db import Base
from ..core.exceptions import SchemaVersionMismatch
from ..core.security import decrypt_secret, encrypt_secret
from ..repositories.catalog import FakeModelRepository
from ..repositories.system import SystemSettingRepository
from ..repositories.users import UserRepository

_SENTINEL_PLAINTEXT = "human-llm-gateway-sentinel"
_SENTINEL_PURPOSE = "sentinel"


class BootstrapService:
    def __init__(self) -> None:
        self.settings_repo = SystemSettingRepository()
        self.users = UserRepository()
        self.models = FakeModelRepository()

    def initialize(self, session: Session, settings: Settings) -> None:
        # 在 session 的 bind 上建表（生产为模块 engine，测试可注入内存库）。
        from .. import repositories  # noqa: F401  # 确保模型注册

        Base.metadata.create_all(bind=session.get_bind())

        stored_version = self.settings_repo.get_json(session, "schema_version")
        if stored_version is not None:
            if stored_version != SCHEMA_VERSION:
                raise SchemaVersionMismatch(
                    f"数据库 schema_version={stored_version} 与代码 {SCHEMA_VERSION} 不一致，"
                    "请备份后重新初始化"
                )
            # 已有数据库：校验 sentinel，不重复种子。
            self._verify_sentinel(session, settings.app_secret)
            return

        self._seed(session, settings)

    def _seed(self, session: Session, settings: Settings) -> None:
        # 1. schema_version 与加密自检 sentinel。
        self.settings_repo.set(session, "schema_version", SCHEMA_VERSION)
        ciphertext = encrypt_secret(_SENTINEL_PLAINTEXT, settings.app_secret, _SENTINEL_PURPOSE)
        self.settings_repo.set(session, "encryption_sentinel", {"ciphertext": ciphertext})

        # 2. 首个管理员（幂等：已存在则不覆盖密码）。
        from .user_service import UserService

        user_service = UserService()
        if self.users.get_by_username(session, settings.admin_username) is None:
            user_service.create_admin(
                session,
                username=settings.admin_username,
                display_name=settings.admin_username,
                password=settings.admin_password,
                must_change_password=True,
            )
        admin = self.users.get_by_username(session, settings.admin_username)

        # 3. 默认系统 Fake Model。
        if admin is not None:
            self.models.seed_default_system_models(session, admin.id)

        session.commit()

    def _verify_sentinel(self, session: Session, app_secret: str) -> None:
        value = self.settings_repo.get_json(session, "encryption_sentinel")
        if not isinstance(value, dict) or "ciphertext" not in value:
            raise SchemaVersionMismatch("数据库缺少加密自检 sentinel")
        plaintext = decrypt_secret(value["ciphertext"], app_secret, _SENTINEL_PURPOSE)
        if plaintext != _SENTINEL_PLAINTEXT:
            raise SchemaVersionMismatch("加密自检 sentinel 校验失败")
