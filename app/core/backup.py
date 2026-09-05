"""SQLite 在线备份与恢复校验；目标必须是新文件，绝不覆盖运行中的数据库。"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy.engine import make_url

from .constants import SCHEMA_VERSION
from .security import decrypt_secret


def sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("备份命令只支持磁盘 SQLite 数据库")
    return Path(url.database).resolve()


def backup_database(source: Path, destination: Path) -> Path:
    """使用 SQLite backup API 取得包含已提交 WAL 的一致性快照。"""
    source = source.resolve(strict=True)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 独占创建避免拼错路径时覆盖已有备份或源数据库。
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader,
            closing(sqlite3.connect(destination)) as writer,
        ):
            reader.backup(writer, pages=128)
            if writer.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("数据库完整性检查失败")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def verify_backup(path: Path, app_secret: str) -> None:
    """只读校验完整性、Schema 版本和加密 sentinel，密钥不写入备份。"""
    path = path.resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("数据库完整性检查失败")
        rows = dict(
            connection.execute(
                "SELECT key, value_json FROM system_settings "
                "WHERE key IN ('schema_version', 'encryption_sentinel')"
            )
        )
    if json.loads(rows.get("schema_version", "null")) != SCHEMA_VERSION:
        raise ValueError("备份 Schema 与当前代码不一致，请使用匹配版本恢复")
    sentinel = json.loads(rows.get("encryption_sentinel", "null"))
    if not isinstance(sentinel, dict) or not isinstance(sentinel.get("ciphertext"), str):
        raise TypeError("备份缺少加密自检 sentinel")
    plaintext = decrypt_secret(sentinel["ciphertext"], app_secret, "sentinel")
    if plaintext != "human-llm-gateway-sentinel":
        raise ValueError("加密自检 sentinel 不匹配")


def restore_database(source: Path, destination: Path, app_secret: str) -> Path:
    """先校验原备份，再恢复到新路径；部署者停服后切换 DATABASE_URL。"""
    verify_backup(source, app_secret)
    restored = backup_database(source, destination)
    try:
        verify_backup(restored, app_secret)
    except BaseException:
        restored.unlink(missing_ok=True)
        raise
    return restored
