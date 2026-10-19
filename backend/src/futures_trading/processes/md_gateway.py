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
import logging

from ..common.instrument_registry import get_instrument
from ..trading.infrastructure.ctp.adapters import CtpConfig, CtpMarketData
from ..trading.infrastructure.messaging.bus import MessageBus
from .base import ProcessBase

logger = logging.getLogger(__name__)

# 注意：replay 源（pandas/pyarrow）不在此处模块级 import——
# openctp 的 C++ 扩展与 pandas 的 C 运行时同进程存在冲突（segfault），
# ctp 源绝不加载 pandas；replay 源在 main() 内懒加载。

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


async def _run_ctp(md: CtpMarketData) -> None:
    """asyncio 部分：运行已创建好的 CtpMarketData（create() 须在主帧先调用）。"""
    logger.info("CtpMarketData 运行中")
    try:
        await md.run()
    finally:
        logger.info("CTP 源退出，stop() ...")
        await md.stop()
        await md.bus.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
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
        # CTP api 创建必须在普通函数帧（main）——SWIG segfault 约束，见 adapters.create
        logger.info("CTP 源启动: contracts=%s interval=%s", symbols, args.interval)
        instruments = {c: get_instrument(_symbol_of(c)) for c in symbols}
        md = CtpMarketData.create(
            CtpConfig.from_settings(),
            MessageBus(),
            instruments,
            interval_minutes=_INTERVAL_MINUTES.get(args.interval, 1),
        )
        md.subscribe(symbols)
        asyncio.run(_run_ctp(md))
        return

    # replay 源懒加载（pandas 链，见模块头说明）
    from ..trading.infrastructure.replay.replay_market_data import replay  # noqa: PLC0415

    for symbol in symbols:
        get_instrument(symbol)  # 注册表校验
    asyncio.run(
        replay(symbols, args.interval, mode=args.mode, speed=args.speed, start=args.start, end=args.end)
    )


if __name__ == "__main__":
    main()
