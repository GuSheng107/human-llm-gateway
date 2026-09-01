"""请求级上下文（request_id / trace_id）与结构化日志。

trace_id 即 request_id（单一 ID 贯穿全链路）。日志通过异步队列批量落库
（app_logs），事件循环内调用不再阻塞等待 SQLite 写锁；普通 logging 记录
通过 ``install_persistence`` 挂接 root logger，同样进入异步队列。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from contextvars import ContextVar, Token
from datetime import UTC
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()


def get_trace_id() -> str | None:
    """trace_id 与 request_id 同源；后台任务无请求上下文时返回 None。"""
    return _request_id.get()


def bind_trace_id(trace_id: str) -> Token[str | None]:
    """在后台任务（无 HTTP 上下文）中显式绑定 trace_id。"""
    return _request_id.set(trace_id)


def new_trace_id() -> str:
    import uuid

    return f"req_{uuid.uuid4().hex[:24]}"


_STRUCTURED = logging.getLogger("human_llm_gateway")

_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set_cookie",
        "password",
        "passwd",
        "credentials",
        "credential",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "session_token",
        "client_secret",
        "secret",
        "password_hash",
        "token_hash",
        "key_hash",
        "code_hash",
        "config_ciphertext",
        "secret_ciphertext",
        "invitation_code",
        "binding_code",
        "temporary_password",
        "avatar_base64",
        "code",
    }
)
_SENSITIVE_SUFFIXES = (
    "_password",
    "_passwd",
    "_secret",
    "_token",
    "_cookie",
    "_credential",
    "_credentials",
)
_REDACTED = "[REDACTED]"
_MAX_MESSAGE_LENGTH = 1000
_MAX_CONTEXT_LENGTH = 4000


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_EXACT_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES):
        return True
    return "api_key" in normalized and not normalized.endswith(("_id", "_prefix"))


def sanitize_log_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_sensitive_key(key) else sanitize_log_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_log_value(item) for item in value]
    return value


def sanitize_log_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return sanitize_log_value(fields)


def _configure_logging() -> None:
    if _STRUCTURED.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _STRUCTURED.addHandler(handler)
    _STRUCTURED.setLevel(logging.INFO)


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def log_event(level: str, event: str, message: str, **fields: object) -> None:
    """结构化日志：stderr JSON 一行 + 异步队列批量落库。

    resource ID 等关联字段以关键字传入（user_id / task_id / api_key_id /
    connection_id / connector_id / trace_id 等），缺失省略。异步上下文中
    调用安全：持久化仅入队，不等待 SQLite 写锁。
    """
    _configure_logging()
    record: dict[str, object] = {
        "level": level,
        "event": event,
        "message": message,
    }
    request_id = get_request_id()
    if request_id is not None:
        record["request_id"] = request_id
    record.update(sanitize_log_fields(fields))
    _STRUCTURED.log(
        getattr(logging, level.upper(), logging.INFO),
        "%s",
        json.dumps(record, ensure_ascii=False, default=str),
    )
    _enqueue_log(level, event, message, request_id, record)


# ----------------------------------------------------------------------
# 异步落库队列：线程安全入队 + 专用线程批量 INSERT
# ----------------------------------------------------------------------


class _LogQueue:
    """日志持久化队列：线程安全入队，专用线程批量写库。

    - 入队永不阻塞、永不抛错（日志失败绝不影响业务主链路）。
    - 队列满（例如数据库长时间锁死）时丢弃最旧条目并计数。
    - 无事件循环依赖：线程上下文与协程上下文均可直接调用。
    - ``_LogStore``（tests/conftest 内存库）绕过队列同步直写，测试无队列干扰。
    """

    _MAX_QUEUE = 2000
    _BATCH_SIZE = 50
    _FLUSH_INTERVAL_SECONDS = 1.0
    _DROPPED_NOTIFY_EVERY = 100

    def __init__(self) -> None:
        self._queue: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._dropped = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # _LogStore 直写模式（测试环境注入内存库时启用）。
        self.direct_store: Any | None = None

    def enqueue(self, entry: dict[str, Any]) -> None:
        try:
            if self.direct_store is not None:
                self.direct_store.append(entry)
                return
            with self._lock:
                if len(self._queue) >= self._MAX_QUEUE:
                    self._queue.pop(0)
                    self._dropped += 1
                    if self._dropped % self._DROPPED_NOTIFY_EVERY == 1:
                        _STRUCTURED.warning("app log queue overflow, dropped=%d", self._dropped)
                self._queue.append(entry)
        except Exception:  # noqa: BLE001 - 入队失败静默：日志绝不影响业务
            self._dropped += 1

    def _drain(self) -> list[dict[str, Any]]:
        with self._lock:
            batch = self._queue[: self._BATCH_SIZE]
            del self._queue[: len(batch)]
        return batch

    def _persist_batch(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        try:
            from sqlalchemy import text as _text

            from .db import SessionLocal

            def _int(entry: dict[str, Any], key: str) -> int | None:
                value = entry.get(key)
                if isinstance(value, bool) or value is None:
                    return None
                try:
                    return int(str(value))
                except (TypeError, ValueError):
                    return None

            rows = [
                {
                    "level": entry["level"],
                    "event": entry["event"],
                    "message": entry["message"],
                    "request_id": entry.get("request_id"),
                    "logger": entry.get("logger"),
                    "user_id": _int(entry, "user_id"),
                    "task_id": _int(entry, "task_id"),
                    "api_key_id": _int(entry, "api_key_id"),
                    "connection_id": _int(entry, "connection_id"),
                    "context": json.dumps(
                        entry.get("context") or {}, ensure_ascii=False, default=str
                    ),
                }
                for entry in batch
            ]
            from datetime import datetime as _datetime

            def _created_at(entry: dict[str, Any]) -> _datetime:
                raw = entry.get("created_at")
                if isinstance(raw, (int, float)):
                    return _datetime.fromtimestamp(raw, tz=UTC)
                if isinstance(raw, _datetime):
                    return raw
                return _datetime.now(tz=UTC)

            with SessionLocal() as session:
                session.execute(
                    _text(
                        "INSERT INTO app_logs (level, event, message, request_id, logger,"
                        " user_id, task_id, api_key_id, connection_id, context_json, created_at)"
                        " VALUES (:level, :event, :message, :request_id, :logger, :user_id,"
                        " :task_id, :api_key_id, :connection_id, :context, :created_at)"
                    ),
                    [{**row, "created_at": _created_at(entry)} for row, entry in zip(rows, batch)],
                )
                session.commit()
        except Exception:  # noqa: BLE001
            if _STRUCTURED.isEnabledFor(logging.DEBUG):
                _STRUCTURED.debug("app log persist failed", exc_info=True)

    def run_forever(self) -> None:
        while not self._stop.is_set():
            batch = self._drain()
            if batch:
                self._persist_batch(batch)
                continue
            self._stop.wait(self._FLUSH_INTERVAL_SECONDS)

    def flush_now(self, timeout_seconds: float = 2.0) -> None:
        """立即落库当前队列（应用关闭与测试同步点）。"""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            batch = self._drain()
            if not batch:
                return
            self._persist_batch(batch)

    def start(self) -> None:
        if self._thread is not None or self.direct_store is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run_forever, name="app-log-persister", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.flush_now()


_queue = _LogQueue()


def _enqueue_log(
    level: str,
    event: str,
    message: str,
    request_id: str | None,
    record: dict[str, object],
) -> None:
    context = {
        key: _clip(value, _MAX_CONTEXT_LENGTH)
        for key, value in record.items()
        if key
        not in {
            "level",
            "event",
            "message",
            "request_id",
            "user_id",
            "task_id",
            "api_key_id",
            "connection_id",
        }
    }
    _queue.enqueue(
        {
            "level": level,
            "event": event,
            "message": _clip(message, _MAX_MESSAGE_LENGTH),
            "request_id": request_id,
            "user_id": record.get("user_id"),
            "task_id": record.get("task_id"),
            "api_key_id": record.get("api_key_id"),
            "connection_id": record.get("connection_id"),
            "context": context,
            "created_at": time.time(),
        }
    )


def flush_log_queue(timeout_seconds: float = 2.0) -> None:
    """测试与优雅关闭用的同步点：立即落库队列中的全部日志。"""
    _queue.flush_now(timeout_seconds)


def get_log_queue() -> _LogQueue:
    return _queue


# ----------------------------------------------------------------------
# 普通日志接入持久化：root logger 挂接收集 handler
# ----------------------------------------------------------------------


class _PersistHandler(logging.Handler):
    """把普通 logging 记录（含 logger.exception）转入异步落库队列。"""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._last_emit: dict[str, float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 同一来源 + 同一消息的告警/异常日志 60 秒去重，防看门狗刷库。
            key = f"{record.name}:{record.levelno}:{record.getMessage()}"
            now = time.monotonic()
            last = self._last_emit.get(key)
            if last is not None and now - last < 60.0:
                return
            if len(self._last_emit) > 512:
                self._last_emit.clear()
            self._last_emit[key] = now
            message = record.getMessage()
            fields: dict[str, Any] = {"logger": record.name}
            if record.exc_info:
                fields["exception"] = (
                    _clip(self.format(record), _MAX_CONTEXT_LENGTH)
                    if self.formatter
                    else f"{record.exc_info[0].__name__ if record.exc_info[0] else 'Exception'}"
                )
            request_id = get_request_id()
            level = record.levelname.lower()
            _queue.enqueue(
                {
                    "level": level,
                    "event": "logging.record",
                    "message": _clip(message, _MAX_MESSAGE_LENGTH),
                    "request_id": request_id,
                    "logger": record.name,
                    "context": {
                        key: _clip(value, _MAX_CONTEXT_LENGTH) for key, value in fields.items()
                    },
                    "created_at": time.time(),
                }
            )
        except Exception:  # noqa: BLE001 - handler 失败静默：不得影响原日志链路
            self._last_emit.pop(key, None)


_persist_handler: _PersistHandler | None = None


def install_persistence() -> None:
    """启动日志落库线程并把普通 logging（WARNING+）接入 app_logs。

    在应用 lifespan 启动时调用；测试环境（内存库）调用 ``set_direct_store``。
    """
    _install_persist_handler()
    _queue.start()


def _install_persist_handler() -> None:
    global _persist_handler
    if _persist_handler is None:
        _configure_logging()
        handler = _PersistHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logging.getLogger().addHandler(handler)
        _persist_handler = handler


def set_direct_store(store: Any) -> None:
    """测试注入：日志直接同步写入内存 store（绕过线程与队列）。

    同时挂接 root logger 的持久化 handler，使普通 logging 告警在测试
    环境同样进入 store（与生产行为一致）。
    """
    _queue.stop()
    _queue.direct_store = store
    _install_persist_handler()


def stop_log_persistence() -> None:
    """优雅关闭：停止落库线程并刷完队列。"""
    _queue.stop()


def is_log_persistence_started() -> bool:
    return _queue._thread is not None
