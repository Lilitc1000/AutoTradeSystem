"""strategy_core：策略引擎进程（实盘链路 + 影子模式）。

链路：Redis 总线 md.bar.* → ShadowEngine（策略 → 组合预检 → 仓位 →
风控终检 → 影子成交）→ signal.outcome 广播（观测）。

M2-a：影子模式端到端（行情源 = replay 或 CTP，成交 = BacktestBroker 虚拟账户）。
M2-b：SIM 模式（成交切 CTP）+ 从 PG 加载真实 StrategyInstance。
保留心跳（healthz 监控用）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import redis.asyncio as redis

from ..common.instrument_registry import get_instrument
from ..common.settings import settings
from ..common.types import Price, Quantity
from ..trading.application.dtos import Bar
from ..trading.application.shadow import ShadowConfig, ShadowEngine
from ..trading.infrastructure.messaging.bus import MessageBus
from .base import ProcessBase


class StrategyCore(ProcessBase):
    name = "strategy_core"

    async def _run(self) -> None:
        self.logger.info("策略引擎就绪（M0 空跑；M2-a 起由 --run 参数进入影子模式）")
        while not self.stopped:
            await asyncio.sleep(1)


def _payload_to_bar(payload: dict) -> Bar:
    return Bar(
        symbol=str(payload["symbol"]),
        interval_minutes=int(payload["interval_minutes"]),
        ts=datetime.fromisoformat(str(payload["ts"])),
        open=Price(Decimal(str(payload["open"]))),
        high=Price(Decimal(str(payload["high"]))),
        low=Price(Decimal(str(payload["low"]))),
        close=Price(Decimal(str(payload["close"]))),
        volume=Quantity(Decimal(str(payload["volume"]))),
        open_interest=Quantity(Decimal(str(payload.get("open_interest", 0)))),
    )


async def _heartbeat() -> None:
    """Redis 心跳（healthz 监控用，与 ProcessBase 同款 key/TTL）。"""
    client = redis.from_url(settings.redis_url, decode_responses=True)
    key = f"{settings.redis_key_prefix}:proc:strategy_core:hb"
    try:
        while True:
            await client.set(key, datetime.now(UTC).isoformat(), ex=15)
            await asyncio.sleep(5)
    finally:
        await client.aclose()


async def run_shadow(symbols: list[str], config: ShadowConfig) -> None:
    instruments = {s: get_instrument(s) for s in symbols}
    engine = ShadowEngine(config, instruments)
    bus = MessageBus()
    print(f"[strategy_core] 影子实例 {config.instance_id} 就绪，订阅 {len(symbols)} 个品种", flush=True)
    bar_count = 0
    outcome_count = 0
    heartbeat_task = asyncio.create_task(_heartbeat())
    try:
        async for _channel, payload in bus.subscribe(*[f"md.bar.{s}" for s in symbols]):
            bar = _payload_to_bar(payload)
            results = engine.on_bar(bar)
            bar_count += 1
            for outcome in results:
                outcome_count += 1
                await bus.publish_outcome(
                    {
                        "instance_id": outcome.signal.instance_id,
                        "symbol": outcome.signal.symbol,
                        "direction": str(outcome.signal.direction),
                        "status": outcome.status,
                        "reason": outcome.reason,
                        "lots": outcome.lots,
                        "binding_layer": outcome.binding_layer,
                        "order_id": outcome.order_id,
                        "ts": bar.ts.isoformat(),
                    }
                )
                print(
                    f"[strategy_core] {outcome.status:9} {outcome.signal.symbol} "
                    f"lots={outcome.lots} {outcome.reason or ''}",
                    flush=True,
                )
            if bar_count % 50 == 0:
                print(
                    f"[strategy_core] 已处理 {bar_count} 根 bar，信号 {outcome_count} 个，"
                    f"权益 {engine.portfolio.equity}",
                    flush=True,
                )
    finally:
        heartbeat_task.cancel()
        await bus.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="策略引擎（M2-a 影子模式）")
    parser.add_argument("--symbols", default="rb")
    parser.add_argument("--instance-id", default="shadow-1")
    parser.add_argument("--risk-per-trade-pct", default="0.01")
    parser.add_argument("--params", default="{}", help='JSON，如 {"fast_window":5,"slow_window":30}')
    args = parser.parse_args()

    config = ShadowConfig(
        instance_id=args.instance_id,
        params=json.loads(args.params),
        risk_per_trade_pct=Decimal(args.risk_per_trade_pct),
    )
    asyncio.run(run_shadow([s.strip() for s in args.symbols.split(",") if s.strip()], config))


if __name__ == "__main__":
    main()
