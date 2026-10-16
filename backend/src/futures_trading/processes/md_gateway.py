"""md_gateway：行情网关进程。

双源（M2-d：两源均已可用）：
- replay：Parquet 历史数据回放（影子模式默认源，不需账号）
- ctp：CTP 实时行情（SimNow 账号就绪后启用，需交易时段才有 tick）

两源输出完全一致（Redis 总线 md.bar.{symbol}），下游 strategy_core 无感知。
CTP 源：连接 → 登录 → 订阅 → tick 聚合 → 发总线（CtpMarketData 封装）。
"""

from __future__ import annotations

import argparse
import asyncio

from ..common.instrument_registry import get_instrument
from ..trading.infrastructure.ctp.adapters import CtpConfig, CtpMarketData
from ..trading.infrastructure.messaging.bus import MessageBus
from ..trading.infrastructure.replay.replay_market_data import replay
from .base import ProcessBase

_INTERVAL_MINUTES = {"min1": 1, "min5": 5, "min15": 15, "day": 1440}


class MdGateway(ProcessBase):
    name = "md_gateway"

    async def _run(self) -> None:
        self.logger.info("行情网关就绪（--source replay 回放 / ctp 实时行情）")
        while not self.stopped:
            await asyncio.sleep(1)


def _symbol_of(contract: str) -> str:
    """合约代码 → 品种符号（rb2601 → rb；IF2601 → IF）。"""
    return "".join(ch for ch in contract if not ch.isdigit())


async def _run_ctp(contracts: list[str], interval: str) -> None:
    """CTP 实时行情：订阅 → 聚合 → 发总线（常驻，直到收到停止信号）。"""
    # CTP 订阅用合约代码（rb2601），Instrument 注册表按品种（rb）索引
    instruments = {c: get_instrument(_symbol_of(c)) for c in contracts}
    bus = MessageBus()
    md = CtpMarketData(
        CtpConfig.from_settings(), bus, instruments, interval_minutes=_INTERVAL_MINUTES.get(interval, 1)
    )
    md.subscribe(contracts)
    try:
        await md.start()
        while True:
            await asyncio.sleep(1)
    finally:
        await md.stop()
        await bus.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="行情网关（replay 回放 / ctp 实时）")
    parser.add_argument("--source", default="replay", choices=["replay", "ctp"])
    parser.add_argument("--symbols", default="rb", help="逗号分隔品种（ctp 源用合约代码，如 rb2601）")
    parser.add_argument("--interval", default="day")
    parser.add_argument("--mode", default="fast", choices=["realtime", "fast"],
                        help="realtime=按 bar 间隔 sleep；fast=尽快（验证用）")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（含）")
    parser.add_argument("--end", default=None, help="截止日期 YYYY-MM-DD（含）")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if args.source == "ctp":
        asyncio.run(_run_ctp(symbols, args.interval))
        return

    for symbol in symbols:
        get_instrument(symbol)  # 注册表校验
    asyncio.run(
        replay(symbols, args.interval, mode=args.mode, speed=args.speed, start=args.start, end=args.end)
    )


if __name__ == "__main__":
    main()
