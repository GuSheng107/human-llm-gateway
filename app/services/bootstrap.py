"""应用初始化：建表、Schema 版本校验、sentinel、管理员与默认模型种子。"""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.constants import SCHEMA_VERSION
from ..core.db import Base
from ..core.exceptions import SchemaVersionMismatch
from ..core.security import decrypt_secret, encrypt_secret
from ..repositories.catalog import FakeModelRepository
from ..repositories.system import SystemSettingRepository
from ..repositories.tools import ToolRepository
from ..repositories.users import UserRepository

# 旧库可幂等补齐的列：idempotent ALTER，避免小字段新增阻塞现有用户。
# 顺序敏感：先加新列再写回 sentinel。
_IDEMPOTENT_COLUMN_PATCHES: list[tuple[str, str, str]] = [
    (
        "assistant_messages",
        "kind",
        "VARCHAR(20) NOT NULL DEFAULT 'normal'",
    ),
]


def _apply_idempotent_column_patches(session: Session) -> None:
    """对老库执行幂等的 ADD COLUMN；新库由 create_all 建出，PRAGMA 检测不到。

    若旧库连目标表都未建出，直接跳过：列补齐只发生在已具备该表的老库上。
    shape 校验会在 _validate_schema_shape 阶段统一拒绝增量/不匹配的表结构。
    """
    connection = session.connection()
    if connection.dialect.name != "sqlite":
        return
    existing_tables = {
        row[1]
        for row in connection.exec_driver_sql(
            "SELECT name, name FROM sqlite_master WHERE type='table'"
        ).all()
    }
    for table, column, decl in _IDEMPOTENT_COLUMN_PATCHES:
        if table not in existing_tables:
            continue
        rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").all()
        if any(row[1] == column for row in rows):
            continue
        connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


_SENTINEL_PLAINTEXT = "human-llm-gateway-sentinel"
_SENTINEL_PURPOSE = "sentinel"


class BootstrapService:
    def __init__(self) -> None:
        self.settings_repo = SystemSettingRepository()
        self.users = UserRepository()
        self.models = FakeModelRepository()
        self.tools = ToolRepository()

    def initialize(self, session: Session, settings: Settings) -> None:
        # 在 session 的 bind 上检查/建表（生产为模块 engine，测试可注入内存库）。
        from .. import repositories  # noqa: F401  # 确保模型注册

        bind = session.get_bind()
        inspector = inspect(bind)
        existing_tables = set(inspector.get_table_names())
        if not existing_tables:
            Base.metadata.create_all(bind=bind)
            self._seed(session, settings)
            return

        # 老库先做幂等补列，再走严格的 shape 校验。
        _apply_idempotent_column_patches(session)
        session.commit()
        inspector = inspect(bind)
        existing_tables = set(inspector.get_table_names())

        self._validate_schema_shape(inspector, existing_tables)

        stored_version = self.settings_repo.get_json(session, "schema_version")
        if stored_version != SCHEMA_VERSION:
            displayed = "缺失" if stored_version is None else repr(stored_version)
            raise SchemaVersionMismatch(
                f"数据库 schema_version={displayed} 与代码 {SCHEMA_VERSION} 不一致，"
                "请备份后重新初始化"
            )
        # 已有目标数据库：校验 sentinel，并幂等补齐平台内置工具。
        self._verify_sentinel(session, settings.app_secret)
        self.tools.seed_default_platform_tools(session)
        session.commit()

    def _validate_schema_shape(self, inspector, existing_tables: set[str]) -> None:
        """只读确认现有库就是当前目标 Schema，禁止 create_all 静默补表补列。"""
        expected_tables = set(Base.metadata.tables)
        if existing_tables != expected_tables:
            missing = sorted(expected_tables - existing_tables)
            extra = sorted(existing_tables - expected_tables)
            raise SchemaVersionMismatch(
                "数据库表结构与当前版本不一致，请备份后重新初始化；"
                f"缺少表={missing}，额外表={extra}"
            )

        for table_name, table in Base.metadata.tables.items():
            expected_columns = set(table.columns.keys())
            actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
            if actual_columns != expected_columns:
                missing = sorted(expected_columns - actual_columns)
                extra = sorted(actual_columns - expected_columns)
                raise SchemaVersionMismatch(
                    f"数据库表 {table_name} 的列与当前版本不一致，请备份后重新初始化；"
                    f"缺少列={missing}，额外列={extra}"
                )

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

        # 4. Fake Tool Call 编辑器和工具沙箱共用的平台工具。
        self.tools.seed_default_platform_tools(session)

        session.commit()

    def _verify_sentinel(self, session: Session, app_secret: str) -> None:
        value = self.settings_repo.get_json(session, "encryption_sentinel")
        if not isinstance(value, dict) or "ciphertext" not in value:
            raise SchemaVersionMismatch("数据库缺少加密自检 sentinel")
        plaintext = decrypt_secret(value["ciphertext"], app_secret, _SENTINEL_PURPOSE)
        if plaintext != _SENTINEL_PLAINTEXT:
            raise SchemaVersionMismatch("加密自检 sentinel 校验失败")
