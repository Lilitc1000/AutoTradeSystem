#!/usr/bin/env python3
"""历史行情导入：akshare → Parquet。

用法（容器内）：
    python scripts/fetch_history.py --symbols rb,hc,i --interval day
    python scripts/fetch_history.py --symbols rb --interval 15m

说明：
- day：全周期日线（akshare 主力连续，回测 5-10 年无压力）
- 15m/60m：akshare 分钟线仅回溯约 1-2 年（上游限制，见 PROJECT.md 调研结论）
- 数据落盘 repo/data/bars/{interval}/{symbol}.parquet（gitignore）
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from futures_trading.trading.infrastructure.parquet_store import save_bars  # noqa: E402

INTERVAL_ALIAS = {"day": "day", "15m": "15m", "60m": "60m", "30m": "30m", "5m": "5m"}


def fetch_daily(symbol: str) -> pd.DataFrame | None:
    """akshare 期货主力连续日线。"""
    import akshare as ak

    # 主力连续合约代码：rb0（新浪主力连续）
    df = ak.futures_main_sina(symbol=f"{symbol}0", start_date="20150101")
    if df is None or df.empty:
        return None
    df = df.rename(
        columns={
            "日期": "ts",
            "开盘价": "open",
            "最高价": "high",
            "最低价": "low",
            "收盘价": "close",
            "成交量": "volume",
            "持仓量": "open_interest",
        }
    )
    df["symbol"] = symbol
    df["interval_minutes"] = 1440
    keep = ["symbol", "interval_minutes", "ts", "open", "high", "low", "close", "volume", "open_interest"]
    return df[[c for c in keep if c in df.columns]]


def fetch_minute(symbol: str, period: str) -> pd.DataFrame | None:
    """akshare 期货分钟线（近期约 1-2 年，取决于 symbol 与上游）。"""
    import akshare as ak

    df = ak.futures_zh_minute_sina(symbol=f"{symbol}连续", period=period)
    if df is None or df.empty:
        return None
    df = df.rename(columns={"datetime": "ts", "date": "ts"})
    df["symbol"] = symbol
    df["interval_minutes"] = int(period)
    keep = ["symbol", "interval_minutes", "ts", "open", "high", "low", "close", "volume", "hold"]
    df = df[[c for c in keep if c in df.columns]].rename(columns={"hold": "open_interest"})
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="akshare 期货历史数据 → Parquet")
    parser.add_argument("--symbols", required=True, help="逗号分隔，如 rb,hc,i")
    parser.add_argument("--interval", default="day", choices=list(INTERVAL_ALIAS))
    args = parser.parse_args()

    interval = INTERVAL_ALIAS[args.interval]
    for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        print(f"[fetch] {symbol} ({interval}) ...", flush=True)
        try:
            if interval == "day":
                df = fetch_daily(symbol)
            else:
                period = {"15m": "15", "30m": "30", "60m": "60", "5m": "5"}[interval]
                df = fetch_minute(symbol, period)
        except Exception as exc:  # noqa: BLE001
            print(f"  [失败] {type(exc).__name__}: {exc}")
            continue
        if df is None or df.empty:
            print("  [空] 无数据返回")
            continue
        df["ts"] = pd.to_datetime(df["ts"])
        path = save_bars(df, symbol, interval)
        span = f"{df['ts'].min():%Y-%m-%d} ~ {df['ts'].max():%Y-%m-%d}"
        print(f"  [完成] {len(df)} 根（{span}）-> {path}")
    print("done", datetime.now().isoformat(timespec="seconds"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
