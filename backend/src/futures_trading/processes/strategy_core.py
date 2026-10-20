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
from ..trading.application.dtos import Bar, Tick
from ..trading.application.ports import OrderPort
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
    """Redis 心跳（healthz 监控用，与 ProcessBase 同款 key/TTL）+ 内存采样。"""
    client = redis.from_url(settings.redis_url, decode_responses=True)
    key = f"{settings.redis_key_prefix}:proc:strategy_core:hb"
    mem_key = f"{settings.redis_key_prefix}:proc:strategy_core:mem"
    try:
        while True:
            await client.set(key, datetime.now(UTC).isoformat(), ex=15)
            rss = ProcessBase._rss_kb()
            if rss is not None:
                await client.set(mem_key, rss, ex=15)
            await asyncio.sleep(5)
    finally:
        await client.aclose()


async def run_shadow(symbols: list[str], config: ShadowConfig, mode: str = "shadow") -> None:
    """影子模式（虚拟成交）或 SIM 模式（SimNow 真实成交）。

    SIM 模式差异：
    - order_port = CtpTradeGateway（真实报单，成交由柜台异步回报）
    - engine.external_fills=True：on_bar 不做同步撮合
    - 订阅 trade.fill：把柜台成交应用到持仓（apply_fill）
    """
    instruments = {s: get_instrument(s) for s in symbols}
    gateway = None
    account = None
    latest_bars: dict[str, Bar] = {}  # 执行器定价基准（最新 bar 收盘价）
    if mode == "sim":
        from ..trading.infrastructure.ctp.adapters import (  # noqa: PLC0415 - 懒加载（Linux C++ 扩展）
            CtpAccount,
            CtpConfig,
            CtpTradeGateway,
        )

        gateway = CtpTradeGateway(CtpConfig.from_settings(), MessageBus(), instruments)
        await gateway.start()
        account = CtpAccount(gateway)
        # 执行器接线：策略层的 MARKET/LIMIT 语义由 ExecutionService 翻译为
        # "对手价±N tick 限价 + 未成交撤单重发"（SHFE 无市价单——错误码 79）。
        # 鸭子类型实现 OrderPort，ShadowEngine/pipeline 零改动。
        from ..trading.application.execution import ExecutionService  # noqa: PLC0415

        def _tick_provider(contract: str) -> Tick | None:
            bar = latest_bars.get(contract)
            if bar is None:
                symbol = "".join(ch for ch in contract if ch.isalpha())
                bar = latest_bars.get(symbol)
            if bar is None:
                return None
            return Tick(symbol=contract, ts=bar.ts, last_price=bar.close)

        executor: OrderPort | None = ExecutionService(gateway, _tick_provider, instruments)
    else:
        executor = None

    engine = ShadowEngine(config, instruments, order_port=executor, external_fills=(mode == "sim"))
    bus = MessageBus()
    print(f"[strategy_core] {mode} 实例 {config.instance_id} 就绪，订阅 {len(symbols)} 个品种", flush=True)
    bar_count = 0
    outcome_count = 0
    heartbeat_task = asyncio.create_task(_heartbeat())
    fill_task = asyncio.create_task(_consume_fills(engine, gateway, bus)) if mode == "sim" else None
    try:
        async for _channel, payload in bus.subscribe(*[f"md.bar.{s}" for s in symbols]):
            bar = _payload_to_bar(payload)
            latest_bars[bar.symbol] = bar
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
            # SIM 模式：按柜台资金快照更新权益（持仓由 fill 消费协程应用）
            if mode == "sim" and account is not None and bar_count % 10 == 0:
                engine.set_equity(Decimal(account.balance()))

            if bar_count % 50 == 0:
                print(
                    f"[strategy_core] 已处理 {bar_count} 根 bar，信号 {outcome_count} 个，"
                    f"权益 {engine.portfolio.equity}",
                    flush=True,
                )
    finally:
        heartbeat_task.cancel()
        if fill_task is not None:
            fill_task.cancel()
        if gateway is not None:
            await gateway.stop()
        await bus.close()


async def _consume_fills(engine: ShadowEngine, gateway: object, bus: MessageBus) -> None:
    """SIM 模式：消费 trade.fill（柜台成交回报）→ 应用到持仓。"""
    async for _channel, payload in bus.subscribe("trade.fill"):
        order_id = str(payload.get("order_id", ""))
        query = getattr(gateway, "query", None)
        order = query(order_id) if callable(query) else None
        if order is not None:
            engine.apply_fill(order)
            print(f"[strategy_core] 成交应用 {order_id} {order.status}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="策略引擎（shadow 影子 / sim SimNow 真实成交）")
    parser.add_argument("--symbols", default="rb")
    parser.add_argument("--instance-id", default="shadow-1")
    parser.add_argument("--mode", default="shadow", choices=["shadow", "sim"])
    parser.add_argument("--risk-per-trade-pct", default="0.01")
    parser.add_argument("--params", default="{}", help='JSON，如 {"fast_window":5,"slow_window":30}')
    args = parser.parse_args()

    config = ShadowConfig(
        instance_id=args.instance_id,
        params=json.loads(args.params),
        risk_per_trade_pct=Decimal(args.risk_per_trade_pct),
    )
    asyncio.run(run_shadow([s.strip() for s in args.symbols.split(",") if s.strip()], config, args.mode))


if __name__ == "__main__":
    main()
