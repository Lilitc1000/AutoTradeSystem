"""历史行情 Parquet 存储。

决策（设计文档补充）：bar 数据量大（分钟级百万行/品种），不适合进 PG——
按 symbol × interval 存 Parquet 文件（data/bars/{interval}/{symbol}.parquet，
gitignore）。PG 只存回测任务与结果（backtest_runs / backtest_trades）。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd

from ...common.types import ZERO, Price, Quantity
from ..application.dtos import Bar

DATA_ROOT = Path(__file__).resolve().parents[4] / "data"  # repo/data
BAR_COLUMNS = ["symbol", "interval_minutes", "ts", "open", "high", "low", "close", "volume", "open_interest"]


def _path(symbol: str, interval: str) -> Path:
    safe = symbol.replace("/", "_")
    return DATA_ROOT / "bars" / interval / f"{safe}.parquet"


def save_bars(df: pd.DataFrame, symbol: str, interval: str) -> Path:
    """保存（覆盖）一个品种的 bar 数据。"""
    path = _path(symbol, interval)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    for col in BAR_COLUMNS:
        if col not in out.columns:
            out[col] = None
    out = out[BAR_COLUMNS]
    out.to_parquet(path, index=False)
    return path


def load_bars_df(symbol: str, interval: str) -> pd.DataFrame | None:
    path = _path(symbol, interval)
    if not path.exists():
        return None
    return pd.read_parquet(path)


def list_symbols(interval: str) -> list[str]:
    directory = DATA_ROOT / "bars" / interval
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.parquet"))


def df_to_bars(df: pd.DataFrame) -> list[Bar]:
    """DataFrame → domain Bar 列表（按 ts 升序）。"""
    bars: list[Bar] = []
    for row in df.sort_values("ts").itertuples(index=False):
        bars.append(
            Bar(
                symbol=str(row.symbol),
                interval_minutes=int(row.interval_minutes),
                ts=pd.Timestamp(row.ts).to_pydatetime(),
                open=Price(_dec(row.open)),
                high=Price(_dec(row.high)),
                low=Price(_dec(row.low)),
                close=Price(_dec(row.close)),
                volume=Quantity(_dec(row.volume)),
                open_interest=Quantity(_dec(row.open_interest)) if row.open_interest is not None else Quantity(ZERO),
            )
        )
    return bars


def _dec(value: object) -> Decimal:
    """pandas 标量 → Decimal（None/NaN → 0）。"""
    if value is None:
        return ZERO
    return Decimal(str(value))


def load_bars(symbol: str, interval: str) -> list[Bar] | None:
    """便捷入口：直接读成 domain Bar 列表。"""
    df = load_bars_df(symbol, interval)
    if df is None:
        return None
    return df_to_bars(df)
