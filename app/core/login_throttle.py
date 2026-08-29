"""登录失败节流：限制单一来源在短时间内的连续失败次数。

节流 key 由调用方构造（见 api/auth.py 的 _throttle_source）：反代场景
下 request.client.host 是代理 IP，纯 IP 计数会把全部用户聚合误锁，
因此采用 "client_ip|username" 组合——同一 IP 对不同用户名各自计数，
对同一用户名的爆破仍被窗口上限拦截。
内存上限：不同 key 数超限按最旧淘汰，防长期运行膨胀。
"""

from __future__ import annotations

import threading
import time

_WINDOW_SECONDS = 60.0
_MAX_FAILURES = 10
# 不同 key（ip|username 组合）容量上限，超出按最旧淘汰。
_MAX_TRACKED_KEYS = 50_000
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def _evict_oldest(table: dict[str, list[float]]) -> None:
    """按最近一次失败时间淘汰最旧 key（调用方必须已持锁）。"""
    if not table:
        return
    oldest = min(table, key=lambda key: table[key][-1])
    table.pop(oldest, None)


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
        while len(_failures) > _MAX_TRACKED_KEYS:
            _evict_oldest(_failures)


def reset(source: str) -> None:
    with _lock:
        _failures.pop(source, None)


def retry_after_seconds() -> int:
    return int(_WINDOW_SECONDS)
