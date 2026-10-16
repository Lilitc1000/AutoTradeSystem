"""消息总线：进程间通信（Redis Pub/Sub）。

协议（JSON payload）：
- md.bar.{symbol}    Bar（K 线，聚合完成才广播）
- md.tick.{symbol}   Tick（原始行情）
- trade.order        OrderCommand（strategy_core → trade_gateway）
- trade.fill         FillReport（trade_gateway → strategy_core / api_dashboard）
- signal.outcome     SignalOutcome（观测：每个信号的最终归宿）
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as redis

from ....common.settings import settings


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


class MessageBus:
    """Redis 发布订阅封装（异步）。"""

    def __init__(self, url: str | None = None, prefix: str | None = None) -> None:
        self.url = url or settings.redis_url
        self.prefix = prefix or settings.redis_key_prefix
        self._client: redis.Redis | None = None

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(self.url, decode_responses=True)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def channel(self, name: str) -> str:
        return f"{self.prefix}:{name}"

    # ---- 发布 ----

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        await self.client.publish(self.channel(channel), _dumps(payload))

    async def publish_tick(self, tick: dict[str, Any]) -> None:
        await self.publish(f"md.tick.{tick['symbol']}", tick)

    async def publish_bar(self, bar: dict[str, Any]) -> None:
        await self.publish(f"md.bar.{bar['symbol']}", bar)

    async def publish_order_command(self, command: dict[str, Any]) -> None:
        await self.publish("trade.order", command)

    async def publish_outcome(self, outcome: dict[str, Any]) -> None:
        await self.publish("signal.outcome", outcome)

    # ---- 订阅 ----

    async def subscribe(self, *channels: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """订阅频道，产出 (channel, payload)；频道名自动加前缀。"""
        pubsub = self.client.pubsub()
        await pubsub.subscribe(*[self.channel(c) for c in channels])
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                raw = message["data"]
                try:
                    payload = json.loads(raw) if isinstance(raw, str | bytes) else {}
                except json.JSONDecodeError:
                    continue
                yield message["channel"].removeprefix(f"{self.prefix}:"), payload
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()


__all__ = ["MessageBus"]
