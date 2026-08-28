"""初始化与 Schema 版本测试。"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.constants import SCHEMA_VERSION
from app.core.exceptions import SchemaVersionMismatch
from app.repositories.models import User
from app.repositories.system import SystemSettingRepository
from app.services.bootstrap import BootstrapService


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def settings():
    return Settings(
        app_secret=os.environ["APP_SECRET"],
        admin_username="admin",
        admin_password="correct-horse-battery-staple",
    )


def test_bootstrap_creates_schema_and_seed(engine, settings) -> None:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        BootstrapService().initialize(session, settings)

    with SessionLocal() as session:
        repo = SystemSettingRepository()
        assert repo.get_json(session, "schema_version") == SCHEMA_VERSION
        assert repo.get_json(session, "encryption_sentinel") is not None
        admin = session.query(User).filter(User.username == "admin").one()
        assert admin.role.value == "admin"
        assert admin.must_change_password is True


def test_bootstrap_is_idempotent(engine, settings) -> None:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        BootstrapService().initialize(session, settings)
        BootstrapService().initialize(session, settings)  # 二次启动不重复种子

    with SessionLocal() as session:
        count = session.query(User).filter(User.username == "admin").count()
        assert count == 1


def test_schema_version_mismatch_fails(engine, settings) -> None:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        BootstrapService().initialize(session, settings)
        # 人为篡改 schema_version
        SystemSettingRepository().set(session, "schema_version", SCHEMA_VERSION + 99)
        session.commit()

    with SessionLocal() as session, pytest.raises(SchemaVersionMismatch):
        BootstrapService().initialize(session, settings)


def test_legacy_schema_fails_without_mutation(engine, settings) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE system_settings (key VARCHAR(120) PRIMARY KEY, value TEXT NOT NULL)")
        )

    before = set(inspect(engine).get_table_names())
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session, pytest.raises(SchemaVersionMismatch, match="当前版本"):
        BootstrapService().initialize(session, settings)

    assert set(inspect(engine).get_table_names()) == before == {"system_settings"}
