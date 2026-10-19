"""进程基类：信号处理、优雅退出、Redis 心跳。

四个进程（md_gateway / strategy_core / trade_gateway / api_dashboard）
共用本骨架。心跳写入 Redis（key: {prefix}:proc:{name}:hb，TTL 15s），
供未来的 api_dashboard 健康检查与告警使用。
"""

from __future__ import annotations

import asyncio
import signal
from abc import ABC, abstractmethod
from datetime import UTC, datetime

import redis.asyncio as redis

from ..common.logging import setup_logging
from ..common.settings import settings

HEARTBEAT_INTERVAL_SECONDS = 5
HEARTBEAT_TTL_SECONDS = 15


class ProcessBase(ABC):
    """异步进程骨架。子类实现 _run()。"""

    name: str = "process"

    def __init__(self) -> None:
        self.logger = setup_logging(self.name)
        self._stop = asyncio.Event()
        self._redis: redis.Redis | None = None

    # ---- 生命周期 ----

    def _setup_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_stop, sig)

    def _request_stop(self, sig: signal.Signals) -> None:
        self.logger.info("收到信号 %s，准备优雅退出", sig.name)
        self._stop.set()

    async def start(self) -> None:
        self._setup_signals()
        self._redis = redis.from_url(settings.redis_url, decode_responses=True)
        self.logger.info("进程启动（env=%s）", settings.env)
        try:
            await asyncio.gather(self._run(), self._heartbeat_loop())
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
        self.logger.info("进程已退出")

    def run(self) -> None:
        """同步入口（进程 main）。"""
        asyncio.run(self.start())

    # ---- 心跳 ----

    @staticmethod
    def _rss_kb() -> int | None:
        """进程常驻内存（KB）；非 Linux（无 /proc）返回 None。"""
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
        except OSError:
            return None
        return None

    async def _heartbeat_loop(self) -> None:
        assert self._redis is not None
        key = f"{settings.redis_key_prefix}:proc:{self.name}:hb"
        mem_key = f"{settings.redis_key_prefix}:proc:{self.name}:mem"
        while not self._stop.is_set():
            try:
                await self._redis.set(key, datetime.now(UTC).isoformat(), ex=HEARTBEAT_TTL_SECONDS)
                rss = self._rss_kb()
                if rss is not None:
                    await self._redis.set(mem_key, rss, ex=HEARTBEAT_TTL_SECONDS)
            except Exception as exc:  # noqa: BLE001  # 心跳失败不致命
                self.logger.warning("心跳写入失败: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            except TimeoutError:
                continue

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    @abstractmethod
    async def _run(self) -> None:
        """进程主逻辑；返回即视为正常结束。"""
