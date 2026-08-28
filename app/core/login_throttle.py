"""登录失败节流：限制单一来源在短时间内的连续失败次数。"""

from __future__ import annotations

import threading
import time

_WINDOW_SECONDS = 60.0
_MAX_FAILURES = 10
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def allow(source: str) -> bool:
    """判断来源是否仍可尝试登录，并惰性清理过期记录。"""
    now = time.monotonic()
    with _lock:
        attempts = [stamp for stamp in _failures.get(source, []) if now - stamp < _WINDOW_SECONDS]
        if attempts:
            _failures[source] = attempts
        else:
            _failures.pop(source, None)
        return len(attempts) < _MAX_FAILURES


def record_failure(source: str) -> None:
    now = time.monotonic()
    with _lock:
        attempts = [stamp for stamp in _failures.get(source, []) if now - stamp < _WINDOW_SECONDS]
        attempts.append(now)
        _failures[source] = attempts


def reset(source: str) -> None:
    with _lock:
        _failures.pop(source, None)


def retry_after_seconds() -> int:
    return int(_WINDOW_SECONDS)
