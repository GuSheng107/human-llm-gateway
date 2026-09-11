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


# ----------------------------------------------------------------------
# 当前操作用户上下文：鉴权成功后绑定，log_event / 落库自动携带
# ----------------------------------------------------------------------

_log_user_id: ContextVar[int | None] = ContextVar("log_user_id", default=None)
_log_user_role: ContextVar[str | None] = ContextVar("log_user_role", default=None)


def bind_log_user(user_id: int, role: str | None) -> None:
    """绑定当前操作者（尽力而为）。

    FastAPI 同步依赖经 run_in_threadpool 执行，ContextVar 设置在每次调用的
    Context 副本中完成、请求结束随副本销毁，无需也不能跨 Context 用 Token
    复位。用户身份同时由鉴权依赖写入 request.scope.state["log_user"]，
    由请求中间件在结束时显式携带进访问日志（见 api/errors.py）。
    """
    _log_user_id.set(user_id)
    _log_user_role.set(role)


def get_log_user_id() -> int | None:
    return _log_user_id.get()


def get_log_user_role() -> str | None:
    return _log_user_role.get()


_STRUCTURED = logging.getLogger("human_llm_gateway")

_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "password",
        "passwd",
        "credentials",
        "credential",
        "api_key",
        "apikey",
        "x_api_key",
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
    "_signature",
)
_REDACTED = "[REDACTED]"
# 数据 URL（base64 内联二进制）在日志中的占位摘要前缀。
_DATA_URL_PLACEHOLDER = "[DATA-URL-OMITTED"


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_EXACT_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES):
        return True
    return "api_key" in normalized and not normalized.endswith(("_id", "_prefix"))


def _sanitize_data_url(value: str) -> str:
    """把 data URL 替换为「媒体类型 + 字节长度」摘要；正文绝不落日志。"""
    if not value.startswith("data:") or "," not in value:
        return value
    header = value.split(",", 1)[0]
    media_type = header[5:].split(";", 1)[0] or "application/octet-stream"
    payload = value.split(",", 1)[1]
    approx_bytes = max(0, len(payload) * 3 // 4)
    return f"{_DATA_URL_PLACEHOLDER} media_type={media_type} approx_bytes={approx_bytes}]"


def sanitize_log_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_sensitive_key(key) else sanitize_log_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_log_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_data_url(value) if value.startswith("data:") else value
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
    # log_event 自带异步落库路径；禁止向 root 传播，否则 _PersistHandler
    # 会把同一条记录再落库一次（event="logging.record" 的重复行）。
    _STRUCTURED.propagate = False


# ----------------------------------------------------------------------
# 日志详情信封（LogDetailEnvelope）：完整的脱敏诊断正文，懒加载展示
# ----------------------------------------------------------------------

_DETAIL_SCHEMA_VERSION = 1
_DETAIL_SECTION_FORMATS = frozenset({"json", "text", "jsonl"})
_TRACEBACK_MARKERS = ("Traceback (most recent call last):", 'File "', ", line ")


def build_log_detail_envelope(
    category: str,
    sections: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造版本化 LogDetailEnvelope（§11.1）。

    sections：[{key, title, format(json|text|jsonl), data}]；数据在落库前
    递归脱敏（调用方也可提前脱敏，这里兜底）。完整存储不截断；仅在超过
    APP_LOG_DETAIL_GUARD_BYTES 病态防御上限时整体丢弃正文并显式标记
    detail_truncated（绝不静默）。
    """
    normalized_sections: list[dict[str, Any]] = []
    for index, section in enumerate(sections or []):
        if not isinstance(section, dict):
            continue
        fmt = str(section.get("format") or "json")
        if fmt not in _DETAIL_SECTION_FORMATS:
            fmt = "text"
        data = sanitize_log_value(section.get("data"))
        normalized_sections.append(
            {
                "key": str(section.get("key") or f"section_{index}"),
                "title": str(section.get("title") or section.get("key") or f"section_{index}"),
                "format": fmt,
                "data": data,
                "redacted_fields": int(_count_redacted(sanitize_log_value(section.get("data")))),
            }
        )
    return {
        "schema_version": _DETAIL_SCHEMA_VERSION,
        "category": category,
        "sections": normalized_sections,
        "links": [
            {
                "kind": str(link.get("kind") or ""),
                "id": str(link.get("id") or ""),
                "label": str(link.get("label") or ""),
            }
            for link in (links or [])
            if isinstance(link, dict)
        ],
    }


def _count_redacted(value: Any) -> int:
    if isinstance(value, dict):
        count = sum(1 for key in value if _is_sensitive_key(key))
        return count + sum(_count_redacted(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_count_redacted(item) for item in value)
    return 0


def looks_like_traceback(message: str) -> bool:
    return any(marker in message for marker in _TRACEBACK_MARKERS) or message.count("\n") > 6


def split_traceback_message(message: str) -> tuple[str, dict[str, Any] | None]:
    """异常堆栈进入详情，列表 message 只保留一行可读摘要（§11.2）。"""
    if not looks_like_traceback(message):
        return message, None
    lines = [line for line in message.splitlines() if line.strip()]
    first_error_line = next(
        (line for line in lines if line.startswith("Error")),
        lines[0] if lines else "异常堆栈已记录到详情",
    )
    detail = build_log_detail_envelope(
        "exception",
        sections=[{"key": "exception", "title": "异常堆栈", "format": "text", "data": message}],
    )
    summary = f"异常堆栈已记录到详情（{first_error_line[:200]}）"
    return summary, detail


def _guard_detail(envelope: dict[str, Any] | None) -> tuple[str | None, int, bool]:
    """防御上限：正常不截断；超限整体丢弃正文并显式标记（不静默）。"""
    if envelope is None:
        return None, 0, False
    from .constants import APP_LOG_DETAIL_GUARD_BYTES

    encoded = json.dumps(envelope, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) <= APP_LOG_DETAIL_GUARD_BYTES:
        return encoded, len(encoded.encode("utf-8")), False
    guarded = {
        "schema_version": envelope.get("schema_version", _DETAIL_SCHEMA_VERSION),
        "category": envelope.get("category", "unknown"),
        "sections": [],
        "links": envelope.get("links", []),
        "guard": {
            "dropped": True,
            "original_bytes": len(encoded.encode("utf-8")),
            "reason": "超过单条详情防御上限，正文已丢弃（未截断保留部分）",
        },
    }
    encoded = json.dumps(guarded, ensure_ascii=False, default=str)
    return encoded, len(encoded.encode("utf-8")), True


def log_event(level: str, event: str, message: str, **fields: object) -> None:
    """结构化日志：stderr JSON 一行 + 异步队列批量落库（不截断，完整存储）。

    - ``detail``：可选的 LogDetailEnvelope（dict）。落库前递归脱敏并做
      病态防御上限检查；stderr 一行只输出轻量 context，不重复打印详情。
    - resource ID 等关联字段以关键字传入（user_id / task_id / api_key_id /
      connection_id / connector_id 等），缺失省略。异步上下文中调用安全。
    """
    _configure_logging()
    detail_raw = fields.pop("detail", None)
    envelope: dict[str, Any] | None = None
    if isinstance(detail_raw, dict):
        envelope = build_log_detail_envelope(
            str(detail_raw.get("category") or "app"),
            sections=detail_raw.get("sections"),
            links=detail_raw.get("links"),
        )
    message, traceback_envelope = split_traceback_message(message)
    if traceback_envelope is not None and envelope is None:
        envelope = traceback_envelope
    record: dict[str, object] = {
        "level": level,
        "event": event,
        "message": message,
    }
    request_id = get_request_id()
    if request_id is not None:
        record["request_id"] = request_id
    if "user_id" not in fields:
        user_id = get_log_user_id()
        if user_id is not None:
            record["user_id"] = user_id
    if "role" not in fields:
        role = get_log_user_role()
        if role is not None:
            record["role"] = role
    record.update(sanitize_log_fields(fields))
    _STRUCTURED.log(
        getattr(logging, level.upper(), logging.INFO),
        "%s",
        json.dumps(record, ensure_ascii=False, default=str),
    )
    _enqueue_log(level, event, message, request_id, record, envelope)


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
                    "detail_json": entry.get("detail_json"),
                    "detail_size_bytes": int(entry.get("detail_size_bytes") or 0),
                    "detail_truncated": bool(entry.get("detail_truncated")),
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
                        " user_id, task_id, api_key_id, connection_id, context_json,"
                        " detail_json, detail_size_bytes, detail_truncated, created_at)"
                        " VALUES (:level, :event, :message, :request_id, :logger, :user_id,"
                        " :task_id, :api_key_id, :connection_id, :context, :detail_json,"
                        " :detail_size_bytes, :detail_truncated, :created_at)"
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

# context 之外的保留键（进独立列，不进 context_json）。
_RESERVED_KEYS = frozenset(
    {
        "level",
        "event",
        "message",
        "request_id",
        "user_id",
        "task_id",
        "api_key_id",
        "connection_id",
    }
)


def _enqueue_log(
    level: str,
    event: str,
    message: str,
    request_id: str | None,
    record: dict[str, object],
    envelope: dict[str, Any] | None = None,
) -> None:
    context = {key: value for key, value in record.items() if key not in _RESERVED_KEYS}
    detail_json, detail_size, detail_truncated = _guard_detail(envelope)
    _queue.enqueue(
        {
            "level": level,
            "event": event,
            "message": message,
            "request_id": request_id,
            "user_id": record.get("user_id"),
            "task_id": record.get("task_id"),
            "api_key_id": record.get("api_key_id"),
            "connection_id": record.get("connection_id"),
            "context": context,
            "detail_json": detail_json,
            "detail_size_bytes": detail_size,
            "detail_truncated": detail_truncated,
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
    """把普通 logging 记录（INFO+，含 logger.exception）转入异步落库队列。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
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
            envelope: dict[str, Any] | None = None
            if record.exc_info:
                # 异常堆栈完整进入详情（不截断）；列表 message 只留摘要。
                full_text = self.format(record) if self.formatter else message
                message, envelope = split_traceback_message(full_text)
            request_id = get_request_id()
            level = record.levelname.lower()
            context_fields = dict(fields)
            role = get_log_user_role()
            if role is not None:
                context_fields["role"] = role
            detail_json, detail_size, detail_truncated = _guard_detail(envelope)
            _queue.enqueue(
                {
                    "level": level,
                    "event": "logging.record",
                    "message": message,
                    "request_id": request_id,
                    "user_id": get_log_user_id(),
                    "logger": record.name,
                    "context": context_fields,
                    "detail_json": detail_json,
                    "detail_size_bytes": detail_size,
                    "detail_truncated": detail_truncated,
                    "created_at": time.time(),
                }
            )
        except Exception:  # noqa: BLE001 - handler 失败静默：不得影响原日志链路
            self._last_emit.pop(key, None)


_persist_handler: _PersistHandler | None = None


def install_persistence() -> None:
    """启动日志落库线程并把普通 logging（INFO+）接入 app_logs。

    在应用 lifespan 启动时调用；测试环境（内存库）调用 ``set_direct_store``。
    root logger 默认 level 为 WARNING：NOTSET 子 logger 的 INFO 记录会
    在源头被 isEnabledFor 丢弃、根本到不了 handler，故此处同步放开
    root level，handler level 才能实际生效。
    """
    _install_persist_handler()
    _queue.start()


def _install_persist_handler() -> None:
    global _persist_handler
    if _persist_handler is None:
        _configure_logging()
        handler = _PersistHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
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
