"""md_gateway：行情网关进程。

双源（M2-a）：
- replay：Parquet 历史数据回放（现在可用，影子模式默认源）
- ctp：CTP 实时行情（M2-b 联调，需 SimNow 账号）

两源输出完全一致（Redis 总线 md.bar.{symbol}），下游无感知。
保留心跳（healthz 监控用）。
"""

from __future__ import annotations

import argparse
import asyncio

from ..common.instrument_registry import get_instrument
from ..trading.infrastructure.replay.replay_market_data import replay
from .base import ProcessBase


class MdGateway(ProcessBase):
    name = "md_gateway"

    async def _run(self) -> None:
        self.logger.info("行情网关就绪（--source replay 回放 / ctp 待 M2-b）")
        while not self.stopped:
            await asyncio.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="行情网关（M2-a：replay 源）")
    parser.add_argument("--source", default="replay", choices=["replay", "ctp"])
    parser.add_argument("--symbols", default="rb", help="逗号分隔品种")
    parser.add_argument("--interval", default="day")
    parser.add_argument("--mode", default="fast", choices=["realtime", "fast"],
                        help="realtime=按 bar 间隔 sleep；fast=尽快（验证用）")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（含）")
    parser.add_argument("--end", default=None, help="截止日期 YYYY-MM-DD（含）")
    args = parser.parse_args()

    if args.source == "ctp":
        raise SystemExit("CTP 源待 M2-b 联调（SimNow 账号就绪后启用）；现在请用 --source replay")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    for symbol in symbols:
        get_instrument(symbol)  # 注册表校验
    asyncio.run(
        replay(symbols, args.interval, mode=args.mode, speed=args.speed, start=args.start, end=args.end)
    )


if __name__ == "__main__":
    main()
