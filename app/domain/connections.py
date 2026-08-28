"""IM 连接领域规则：错误类别与带抖动指数退避。

见 docs/DATABASE.md §4.1 与 docs/ARCHITECTURE.md §8：
普通网络错误按带抖动的指数退避自动重连；认证失效进入 auth_required
停止重试并等待所有者重新登录；手动停止不被后台重试拉起。
"""

from __future__ import annotations

import random
from collections.abc import Callable

from ..core.constants import (
    CONNECTION_BACKOFF_BASE_SECONDS,
    CONNECTION_BACKOFF_JITTER_RATIO,
    CONNECTION_BACKOFF_MAX_SECONDS,
)

# 脱敏错误类别（写入 im_connections.last_error_code）。
ERROR_NETWORK = "network_error"
ERROR_AUTH = "auth_required"
ERROR_CONFIG = "config_invalid"
ERROR_DELIVERY = "delivery_failed"


class ConnectorError(Exception):
    """连接器运行错误；code 决定重试策略（网络重试 / 认证停止）。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code

    @property
    def is_auth(self) -> bool:
        return self.code == ERROR_AUTH

    @property
    def is_config(self) -> bool:
        return self.code == ERROR_CONFIG


def backoff_delay(
    retry_count: int,
    *,
    random_fn: Callable[[], float] = random.random,
    base: float = CONNECTION_BACKOFF_BASE_SECONDS,
    max_delay: float = CONNECTION_BACKOFF_MAX_SECONDS,
    jitter_ratio: float = CONNECTION_BACKOFF_JITTER_RATIO,
) -> float:
    """第 retry_count 次重试前的等待秒数。

    delay = min(max, base * 2**retry_count)，再叠加 ±jitter_ratio 比例的随机抖动，
    避免大量连接在同一时刻同步重连（thundering herd）。
    """
    raw = min(max_delay, base * (2 ** max(0, retry_count)))
    jitter = 1.0 + (random_fn() * 2 - 1) * jitter_ratio
    return max(0.0, raw * jitter)
