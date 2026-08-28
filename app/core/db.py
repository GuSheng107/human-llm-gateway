"""数据库引擎与会话。

只负责引擎、Session 工厂和建表；Schema 版本校验、种子与 sentinel 由
services.bootstrap 编排。SQLite 启用外键、WAL 和 busy timeout。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        return engine
    return create_engine(url)


settings = get_settings()
engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def begin_immediate_if_sqlite(session: Session) -> None:
    """关键 SQLite 写用例在任何读取前取得写锁，避免并发超卖。"""
    if session.get_bind().dialect.name == "sqlite" and not session.in_transaction():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def create_schema() -> None:
    """按目标模型创建全部表和索引（不写入种子）。"""
    from ..repositories import models  # noqa: F401  # 确保所有模型已注册

    Base.metadata.create_all(bind=engine)
