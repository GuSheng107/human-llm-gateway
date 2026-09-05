"""部署者本机维护入口：backup / verify-backup / restore。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .core.backup import backup_database, restore_database, sqlite_path, verify_backup
from .core.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Human LLM Gateway 数据库备份与恢复")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="在线备份当前 SQLite 数据库")
    backup.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify-backup", help="校验完整性与 APP_SECRET")
    verify.add_argument("--path", type=Path, required=True)
    restore = commands.add_parser("restore", help="恢复到新文件，不覆盖现有数据库")
    restore.add_argument("--source", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "backup":
        result = backup_database(sqlite_path(settings.database_url), args.output)
        print(f"备份完成：{result}")
    elif args.command == "verify-backup":
        verify_backup(args.path, settings.app_secret)
        print("备份校验通过：数据库完整，Schema 与主密钥匹配")
    else:
        result = restore_database(args.source, args.output, settings.app_secret)
        print(f"恢复完成：{result}；停服后将 DATABASE_URL 指向该文件再启动")


if __name__ == "__main__":
    main()
