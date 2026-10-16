"""ProcessBase 测试（无 db 依赖）。

心跳用 FakeRedis 验证 key 格式与 TTL；优雅退出验证 stop event。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from futures_trading.common.settings import settings
from futures_trading.processes.base import HEARTBEAT_TTL_SECONDS, ProcessBase


class FakeRedis:
    """最小 async redis 替身：记录 set 调用。"""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int | None]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = (value, ex)

    async def aclose(self) -> None:
        pass


class _StubProcess(ProcessBase):
    name = "stub"

    def __init__(self) -> None:
        super().__init__()
        self.fake = FakeRedis()

    async def _run(self) -> None:
        await self._stop.wait()

    async def start(self) -> None:  # 跳过真实 redis 与信号注册
        self._redis = self.fake  # type: ignore[assignment]
        await asyncio.gather(self._run(), self._heartbeat_loop())
        await self._cleanup()


@pytest.mark.asyncio
async def test_heartbeat_key_and_ttl() -> None:
    import signal as signal_mod

    process = _StubProcess()
    task = asyncio.create_task(process.start())
    await asyncio.sleep(0.1)  # 等第一轮心跳
    process._request_stop(signal_mod.SIGTERM)
    await asyncio.wait_for(task, timeout=2)

    key = f"{settings.redis_key_prefix}:proc:stub:hb"
    assert key in process.fake.store
    value, ttl = process.fake.store[key]
    assert ttl == HEARTBEAT_TTL_SECONDS
    datetime.fromisoformat(value)  # 可解析为 ISO 时间


@pytest.mark.asyncio
async def test_graceful_stop_sets_event() -> None:
    import signal as signal_mod

    process = _StubProcess()
    task = asyncio.create_task(process.start())
    await asyncio.sleep(0.05)
    process._request_stop(signal_mod.SIGTERM)
    await asyncio.wait_for(task, timeout=2)
    assert process.stopped
