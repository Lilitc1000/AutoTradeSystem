"""参数网格扫描（设计文档 7.3：v1 限制最多 2 参数，防止组合空间爆炸）。

每个参数组合跑一次完整回测，按夏普排序返回——
参数"平原"（邻域表现相近）而非"尖峰"是过拟合的判据。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import product
from typing import Any

from ..application.runner import run_backtest
from .backtest.engine import BacktestConfig


def _to_int(value: object) -> int:
    """metrics 值安全转 int（report.metrics 是 dict[str, object]）。"""
    if isinstance(value, int):
        return value
    return int(str(value))


@dataclass(frozen=True)
class ScanResult:
    """单个参数组合的回测摘要。"""

    params: dict[str, Any]
    total_return_pct: str
    max_drawdown_pct: str
    sharpe: str
    round_trips: int


def grid_scan(
    config: BacktestConfig,
    bars_by_symbol: dict[str, list],
    instruments: dict,
    grid: dict[str, list[Any]],
    *,
    max_params: int = 2,
) -> list[ScanResult]:
    """对 config.params 做网格扫描；超过 max_params 个参数直接拒绝。"""
    if not grid:
        raise ValueError("扫描网格不能为空")
    if len(grid) > max_params:
        raise ValueError(f"参数个数 {len(grid)} 超过上限 {max_params}（防止组合空间爆炸）")

    keys = list(grid)
    results: list[ScanResult] = []
    for combo in product(*(grid[k] for k in keys)):
        params = {**config.params, **dict(zip(keys, combo, strict=True))}
        run_config = replace(config, params=params)
        report = run_backtest(run_config, bars_by_symbol, instruments, run_id="scan")
        metrics = report.metrics
        results.append(
            ScanResult(
                params=params,
                total_return_pct=str(metrics.get("total_return_pct", "0")),
                max_drawdown_pct=str(metrics.get("max_drawdown_pct", "0")),
                sharpe=str(metrics.get("sharpe", "0")),
                round_trips=_to_int(metrics.get("round_trips", 0)),
            )
        )
    results.sort(key=lambda r: Decimal(r.sharpe), reverse=True)
    return results


__all__ = ["ScanResult", "grid_scan"]
