"""真实 WAL 文件备份、密钥校验和新路径恢复演练。"""

from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy.orm import Session

from app.core.backup import backup_database, restore_database, verify_backup
from app.core.config import Settings
from app.core.db import _make_engine
from app.core.exceptions import SecretCryptoError
from app.core.security import decrypt_secret, encrypt_secret
from app.repositories.models import User
from app.repositories.system import SystemSettingRepository
from app.services.bootstrap import BootstrapService


def test_live_wal_backup_restores_users_and_encrypted_data(tmp_path) -> None:
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"
    settings = Settings(
        app_secret=os.environ["APP_SECRET"], admin_password="Admin-Pass1!", _env_file=None
    )
    engine = _make_engine(f"sqlite:///{source.as_posix()}")
    try:
        with Session(engine) as session:
            BootstrapService().initialize(session, settings)
            encrypted = encrypt_secret("private-upstream-value", settings.app_secret, "test")
            SystemSettingRepository().set(session, "restore_probe", encrypted)
            session.commit()
        assert source.with_name(source.name + "-wal").is_file()
        backup_database(source, backup)
        verify_backup(backup, settings.app_secret)
        restore_database(backup, restored, settings.app_secret)
        restored_engine = _make_engine(f"sqlite:///{restored.as_posix()}")
        try:
            with Session(restored_engine) as session:
                BootstrapService().initialize(session, settings)
                assert session.query(User).filter_by(username=settings.admin_username).one()
                value = SystemSettingRepository().get_json(session, "restore_probe")
                assert (
                    decrypt_secret(value, settings.app_secret, "test") == "private-upstream-value"
                )
        finally:
            restored_engine.dispose()
        with pytest.raises(SecretCryptoError):
            restore_database(backup, tmp_path / "wrong-key.db", secrets.token_urlsafe(32))
        assert not (tmp_path / "wrong-key.db").exists()
    finally:
        engine.dispose()


def test_backup_never_overwrites_existing_files(tmp_path) -> None:
    source = tmp_path / "source.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE example(value TEXT)")
    original = source.read_bytes()
    with pytest.raises(FileExistsError):
        backup_database(source, source)
    assert source.read_bytes() == original
    destination = tmp_path / "existing.db"
    destination.write_bytes(b"existing-backup")
    with pytest.raises(FileExistsError):
        backup_database(source, destination)
    assert destination.read_bytes() == b"existing-backup"


def test_corrupt_backup_cannot_be_restored(tmp_path) -> None:
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not-a-database")
    target = tmp_path / "target.db"
    with pytest.raises(sqlite3.DatabaseError):
        restore_database(corrupt, target, os.environ["APP_SECRET"])
    assert not target.exists()
