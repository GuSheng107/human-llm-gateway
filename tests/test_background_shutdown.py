"""关闭后台清理时，必须等待已开始的数据库线程结束。"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.services.data_retention import DataRetentionService


def test_retention_loop_waits_before_first_periodic_cleanup(monkeypatch) -> None:
    async def scenario() -> None:
        sleep_started = asyncio.Event()
        sleep_release = asyncio.Event()
        cleanup_called = False

        async def controlled_sleep(delay: float) -> None:
            assert delay == 123
            sleep_started.set()
            await sleep_release.wait()

        def cleanup() -> dict[str, int]:
            nonlocal cleanup_called
            cleanup_called = True
            return {}

        service = DataRetentionService()
        monkeypatch.setattr(service, "_cleanup", cleanup)
        monkeypatch.setattr("app.services.data_retention.DATA_RETENTION_INTERVAL_SECONDS", 123)
        monkeypatch.setattr("app.services.data_retention.asyncio.sleep", controlled_sleep)
        runner = asyncio.create_task(service.run())
        await asyncio.wait_for(sleep_started.wait(), timeout=5)
        assert cleanup_called is False
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner

    asyncio.run(scenario())


def test_retention_cancellation_waits_for_running_database_work(monkeypatch) -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        started = loop.create_future()
        release = threading.Event()
        finished = threading.Event()

        def cleanup() -> dict[str, int]:
            loop.call_soon_threadsafe(started.set_result, None)
            try:
                assert release.wait(timeout=5), "清理线程未收到测试释放信号"
                return {}
            finally:
                finished.set()

        service = DataRetentionService()
        monkeypatch.setattr(service, "_cleanup", cleanup)
        monkeypatch.setattr("app.services.data_retention.DATA_RETENTION_INTERVAL_SECONDS", 0)
        runner = asyncio.create_task(service.run())
        try:
            await asyncio.wait_for(started, timeout=5)
            runner.cancel()
            # 让取消信号传播到后台协程，但先不释放数据库线程。
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not runner.done(), "数据库线程仍在工作时，关闭不得提前完成"
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await runner
        assert finished.is_set()

    asyncio.run(scenario())
