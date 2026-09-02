"""M4 运行时测试：退避策略、auth_required 停止重试、隔离与恢复。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.connectors.base import Connector, ConnectorContext, InboundMessage
from app.connectors.manager import ConnectionManager
from app.connectors.registry import ConnectorRegistry, PlatformSpec
from app.core.constants import CONNECTION_BACKOFF_MAX_SECONDS
from app.core.time import utc_now
from app.domain.connections import ERROR_AUTH, ERROR_NETWORK, ConnectorError, backoff_delay
from app.domain.enums import ConnectionState


class _FakeRow:
    """模拟 ImConnection 行（管理器不依赖 ORM 实例类型）。"""

    def __init__(self, connection_id: int, platform: str, retry_count: int = 0) -> None:
        self.id = connection_id
        self.owner_user_id = 1
        self.name = f"conn-{connection_id}"
        self.platform = platform
        self.retry_count = retry_count
        self.desired_running = True


class FlakyConnector(Connector):
    """可控失败的连接器：统计 start 次数，按脚本返回错误。"""

    platform = "flaky"

    def __init__(self, ctx: ConnectorContext) -> None:
        super().__init__(ctx)
        self.stopped = False
        self.closed = asyncio.Event()
        self.script: list[ConnectorError | None] = getattr(ctx, "script", []) or []
        # 每次重连都会新建实例，尝试次数由工厂共享计数器统计。
        self.counter: dict = getattr(ctx, "counter", {}) or {"n": 0}

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> list[str]:
        return []

    async def start(self) -> None:
        attempt = self.counter.get("n", 0) + 1
        self.counter["n"] = attempt
        index = min(attempt - 1, len(self.script) - 1) if self.script else -1
        error = self.script[index] if index >= 0 else None
        if error is not None:
            raise error
        self.closed.clear()

    async def stop(self) -> None:
        self.stopped = True
        self.closed.set()

    async def wait_closed(self) -> None:
        await self.closed.wait()


class Recorder:
    """记录管理器写入的状态补丁。"""

    def __init__(self) -> None:
        self.patches: dict[int, list[dict[str, Any]]] = {}

    async def __call__(self, connection_id: int, patch: dict[str, Any]) -> None:
        self.patches.setdefault(connection_id, []).append(patch)

    def states(self, connection_id: int) -> list[str]:
        return [
            patch["state"].value
            for patch in self.patches.get(connection_id, [])
            if "state" in patch
        ]


def _registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(
        PlatformSpec(
            code="flaky", label="测试连接器", description="", kind="client", supports_delivery=True
        ),
        FlakyConnector,
    )
    return registry


def _manager(registry: ConnectorRegistry, recorder: Recorder) -> ConnectionManager:
    manager = ConnectionManager(registry)
    manager.set_state_recorder(recorder)
    return manager


async def _inbound(connection_id: int, message: InboundMessage):
    return "accepted"


def test_backoff_delay_grows_exponentially_with_jitter_and_cap() -> None:
    delays = [backoff_delay(i, random_fn=lambda: 0.5) for i in range(6)]
    assert delays[0] == pytest.approx(2.0)
    assert delays[1] == pytest.approx(4.0)
    assert delays[3] == pytest.approx(16.0)
    # 抖动存在但被限制在 ±20%。
    low = backoff_delay(4, random_fn=lambda: 0.0)
    high = backoff_delay(4, random_fn=lambda: 1.0)
    assert low == pytest.approx(32 * 0.8)
    assert high == pytest.approx(32 * 1.2)
    assert backoff_delay(20, random_fn=lambda: 0.5) == pytest.approx(CONNECTION_BACKOFF_MAX_SECONDS)


async def test_auth_failure_stops_retry_and_waits_for_owner() -> None:
    recorder = Recorder()
    manager = _manager(_registry(), recorder)
    row = _FakeRow(1, "flaky")
    ctx_script: list = [ConnectorError(ERROR_AUTH, "认证失效")]
    manager._registry.create = _factory_with(ctx_script)  # type: ignore[method-assign]

    await manager.start(row, {}, _inbound)
    await asyncio.sleep(0.05)
    await manager.stop(1)

    assert recorder.states(1)[-1] == ConnectionState.AUTH_REQUIRED.value
    last_patch = recorder.patches[1][-1]
    assert last_patch["last_error_code"] == ERROR_AUTH
    assert last_patch["next_retry_at"] is None
    assert manager.get_instance(1) is None


async def test_network_failure_is_retried_with_scheduled_backoff() -> None:
    recorder = Recorder()
    manager = _manager(_registry(), recorder)
    row = _FakeRow(2, "flaky")
    manager._registry.create = _factory_with([None])  # type: ignore[method-assign]

    await manager.start(row, {}, _inbound)
    await asyncio.sleep(0.05)
    # 两阶段事务后，manager.start 不再写 starting 初始状态（service 层预写）。
    # 仅断言后续 _supervise 落库了 online。
    assert recorder.states(2) == [ConnectionState.ONLINE.value]
    connector = manager.get_instance(2)
    assert connector is not None

    # 模拟连接断开：应记录 error 并安排下一次重试时间。
    connector.closed.set()
    await asyncio.sleep(0.05)
    disconnect_patch = recorder.patches[2][-1]
    assert disconnect_patch["state"] == ConnectionState.ERROR.value
    assert disconnect_patch["retry_count"] == 1
    assert disconnect_patch["next_retry_at"] > utc_now()
    await manager.stop(2)


async def test_start_failure_uses_backoff_and_restarts(monkeypatch) -> None:
    import app.connectors.manager as manager_module

    monkeypatch.setattr(manager_module, "backoff_delay", lambda *args, **kwargs: 0.01)
    recorder = Recorder()
    manager = _manager(_registry(), recorder)
    row = _FakeRow(3, "flaky")
    failures = [ConnectorError(ERROR_NETWORK, "网络不可达"), None]
    manager._registry.create = _factory_with(failures)  # type: ignore[method-assign]

    await manager.start(row, {}, _inbound)
    await asyncio.sleep(0.2)
    # 两阶段事务后，manager.start 不再写 starting 初始状态（service 层预写）。
    # 这里仅断言 error 与最终 online 都被 _supervise 正确落库。
    states = recorder.states(3)
    assert ConnectionState.ERROR.value in states
    assert states[-1] == ConnectionState.ONLINE.value
    error_patch = next(patch for patch in recorder.patches[3] if "retry_count" in patch)
    assert error_patch["retry_count"] == 1
    assert error_patch["next_retry_at"] is not None
    await manager.stop(3)


async def test_stop_is_isolated_to_target_connection() -> None:
    recorder = Recorder()
    manager = _manager(_registry(), recorder)
    manager._registry.create = _factory_with([None])  # type: ignore[method-assign]
    first, second = _FakeRow(4, "flaky"), _FakeRow(5, "flaky")
    await manager.start(first, {}, _inbound)
    await manager.start(second, {}, _inbound)
    await asyncio.sleep(0.05)

    await manager.stop(4)
    assert manager.get_instance(4) is None
    assert manager.get_instance(5) is not None
    await manager.stop(5)
    assert manager.get_instance(5) is None


async def test_apply_restarts_only_target_connection() -> None:
    recorder = Recorder()
    manager = _manager(_registry(), recorder)
    manager._registry.create = _factory_with([None])  # type: ignore[method-assign]
    target, other = _FakeRow(6, "flaky"), _FakeRow(7, "flaky")
    await manager.start(target, {}, _inbound)
    await manager.start(other, {}, _inbound)
    await asyncio.sleep(0.05)
    target_instance = manager.get_instance(6)
    other_instance = manager.get_instance(7)

    await manager.stop(6)
    await manager.start(target, {"restarted": True}, _inbound)
    await asyncio.sleep(0.05)

    assert manager.get_instance(6) is not target_instance
    assert manager.get_instance(7) is other_instance
    await manager.stop_all()


async def test_manual_stop_is_not_pulled_back_by_retry() -> None:
    recorder = Recorder()
    manager = _manager(_registry(), recorder)
    row = _FakeRow(8, "flaky")
    manager._registry.create = _factory_with([None])  # type: ignore[method-assign]
    await manager.start(row, {}, _inbound)
    await asyncio.sleep(0.05)
    stopped = manager.get_instance(8)
    await manager.stop(8)
    # 手动停止后 desired_running=False，恢复流程不得重新拉起。
    row.desired_running = False
    assert await manager.recover_all([row], lambda row: {}, _inbound) == 0
    assert stopped is not None and stopped.stopped is True


async def test_recover_all_restarts_desired_running_connections() -> None:
    recorder = Recorder()
    manager = _manager(_registry(), recorder)
    manager._registry.create = _factory_with([None])  # type: ignore[method-assign]
    rows = [_FakeRow(9, "flaky"), _FakeRow(10, "flaky", retry_count=3)]
    rows[1].desired_running = False
    assert await manager.recover_all(rows, lambda row: {}, _inbound) == 1
    await asyncio.sleep(0.05)
    assert manager.get_instance(9) is not None
    assert manager.get_instance(10) is None
    await manager.stop_all()


def _factory_with(script: list):
    """返回一个 create()，把脚本与共享计数器注入到连接器的 ctx。"""
    counter = {"n": 0}

    def _create(code: str, ctx: ConnectorContext) -> Connector:
        ctx.script = script  # type: ignore[attr-defined]
        ctx.counter = counter  # type: ignore[attr-defined]
        return FlakyConnector(ctx)

    return _create


def test_connector_error_classification_drives_retry_policy() -> None:
    network = ConnectorError(ERROR_NETWORK, "网络错误")
    auth = ConnectorError(ERROR_AUTH, "认证失效")
    assert (network.is_auth, network.is_config) == (False, False)
    assert auth.is_auth is True
    # 认证失效不参与退避重试，网络错误参与。
    assert auth.is_auth is not network.is_auth
