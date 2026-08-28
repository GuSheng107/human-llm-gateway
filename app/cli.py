"""受控管理员 CLI。

用法：
    python -m app.cli admin create --username <name> --display-name <name>
    python -m app.cli admin create --username <name> --display-name <name> --password-stdin --yes
    python -m app.cli admin create --username <name> --display-name <name> --generate-password --yes

密码只从交互式 getpass（两次确认）、stdin 或系统生成三种来源之一读取；
禁止 --password 明文参数。--password-stdin 与 --generate-password 互斥。
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .core.config import get_settings
from .core.db import SessionLocal
from .core.security import generate_temporary_password
from .services.bootstrap import BootstrapService
from .services.user_service import UserService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Human LLM Gateway 管理 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("admin", help="管理管理员")
    admin_sub = create.add_subparsers(dest="admin_command", required=True)
    create_admin = admin_sub.add_parser("create", help="创建管理员")
    create_admin.add_argument("--username", required=True)
    create_admin.add_argument("--display-name", required=True)
    create_admin.add_argument("--password-stdin", action="store_true")
    create_admin.add_argument("--generate-password", action="store_true")
    create_admin.add_argument("--yes", action="store_true", help="确认非交互流程")
    return parser


def _read_password(args: argparse.Namespace) -> tuple[str, bool]:
    if args.password_stdin and args.generate_password:
        print("错误：--password-stdin 与 --generate-password 互斥", file=sys.stderr)
        sys.exit(2)
    if args.password_stdin:
        if not args.yes:
            print("错误：--password-stdin 需要同时指定 --yes", file=sys.stderr)
            sys.exit(2)
        password = sys.stdin.readline().rstrip("\n")
        return password, False
    if args.generate_password:
        if not args.yes:
            print("错误：--generate-password 需要同时指定 --yes", file=sys.stderr)
            sys.exit(2)
        return generate_temporary_password(), True
    password = getpass.getpass("密码: ")
    confirm = getpass.getpass("再次输入密码: ")
    if password != confirm:
        print("错误：两次输入不一致", file=sys.stderr)
        sys.exit(2)
    return password, False


def main() -> None:
    args = _build_parser().parse_args()
    if args.command != "admin" or args.admin_command != "create":
        print("暂不支持该命令", file=sys.stderr)
        sys.exit(2)

    settings = get_settings()
    with SessionLocal() as bootstrap_session:
        BootstrapService().initialize(bootstrap_session, settings)

    password, generated = _read_password(args)

    with SessionLocal() as session:
        user = UserService().create_admin(
            session,
            username=args.username,
            display_name=args.display_name,
            password=password,
            must_change_password=generated,
        )
        session.commit()
        print(f"已创建管理员 {user.username} (id={user.id})")
        if generated:
            print(password)


if __name__ == "__main__":
    main()
