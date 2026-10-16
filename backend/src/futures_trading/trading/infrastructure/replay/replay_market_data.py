"""ReplayMarketData：Parquet 历史数据按时间轴推送（md_gateway 的 replay 源）。

两种模式：
- realtime：按 bar 时间间隔 sleep 推送（模拟实时，影子模式体感真实）
- fast：尽快推送（验证/压测用）

与 CTP 源的输出完全一致（同走 MessageBus.publish_bar）——
strategy_core 感知不到源的区别。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from ....common.logging import setup_logging
from ..messaging.bus import MessageBus
from ..parquet_store import load_bars_df


def bar_to_payload(row: object, symbol: str, interval_minutes: int) -> dict[str, object]:
    """parquet 行 → 总线 payload（与 Bar dataclass 同构）。"""
    return {
        "symbol": symbol,
        "interval_minutes": interval_minutes,
        "ts": str(row.ts),  # type: ignore[attr-defined]
        "open": str(row.open),  # type: ignore[attr-defined]
        "high": str(row.high),  # type: ignore[attr-defined]
        "low": str(row.low),  # type: ignore[attr-defined]
        "close": str(row.close),  # type: ignore[attr-defined]
        "volume": str(row.volume),  # type: ignore[attr-defined]
    }


async def replay(
    symbols: list[str],
    interval: str,
    *,
    mode: str = "realtime",
    speed: float = 1.0,
    start: str | None = None,
    end: str | None = None,
) -> int:
    """推送历史 bar 到 Redis 总线；返回推送根数。"""
    logger = setup_logging("replay")
    bus = MessageBus()
    pushed = 0
    try:
        for symbol in symbols:
            df = load_bars_df(symbol, interval)
            if df is None or df.empty:
                logger.warning("品种 %s 无 %s 数据", symbol, interval)
                continue
            df = df.sort_values("ts")
            if start:
                df = df[df["ts"] >= start]  # type: ignore[arg-type]
            if end:
                df = df[df["ts"] <= end]  # type: ignore[arg-type]
            interval_minutes = int(df["interval_minutes"].iloc[0])
            logger.info(
                "回放 %s（%s）：%d 根 %s ~ %s",
                symbol,
                mode,
                len(df),
                df["ts"].iloc[0],
                df["ts"].iloc[-1],
            )
            prev_ts: datetime | None = None
            for row in df.itertuples(index=False):
                if mode == "realtime" and prev_ts is not None:
                    gap = (row.ts - prev_ts).total_seconds()  # type: ignore[attr-defined]
                    await asyncio.sleep(max(gap / speed, 0))
                await bus.publish_bar(bar_to_payload(row, symbol, interval_minutes))
                prev_ts = row.ts  # type: ignore[attr-defined]
                pushed += 1
    finally:
        await bus.close()
    logger.info("回放完成，共推送 %d 根", pushed)
    return pushed


def main() -> None:
    parser = argparse.ArgumentParser(description="Parquet 历史数据 → Redis 总线（影子模式行情源）")
    parser.add_argument("--symbols", required=True, help="逗号分隔，如 rb,hc")
    parser.add_argument("--interval", default="day")
    parser.add_argument("--mode", default="realtime", choices=["realtime", "fast"])
    parser.add_argument("--speed", type=float, default=1.0, help="realtime 模式倍速")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()
    asyncio.run(
        replay(
            [s.strip() for s in args.symbols.split(",") if s.strip()],
            args.interval,
            mode=args.mode,
            speed=args.speed,
            start=args.start,
            end=args.end,
        )
    )


if __name__ == "__main__":
    main()
