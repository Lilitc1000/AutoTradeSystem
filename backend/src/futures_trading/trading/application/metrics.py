"""回测指标计算（纯函数，两级：实例级 + 组合级）。

设计文档 7.4：实例级含"单笔风险实现值 vs 预算值"（滑点假设失真的早期预警）。
所有函数不碰 IO，输入为引擎产出的权益曲线与成交流水。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

ZERO = Decimal("0")


@dataclass(frozen=True)
class InstanceMetrics:
    """实例级指标。"""

    total_pnl: Decimal = ZERO
    total_return_pct: Decimal = ZERO
    max_drawdown_pct: Decimal = ZERO
    sharpe: Decimal = ZERO
    win_rate_pct: Decimal = ZERO
    profit_factor: Decimal = ZERO
    trade_count: int = 0
    round_trips: int = 0
    avg_win: Decimal = ZERO
    avg_loss: Decimal = ZERO
    avg_loss_vs_budget_pct: Decimal = ZERO  # 平均单笔亏损 / 风险预算（>100% = 滑点假设失真）
    bars_per_year: Decimal = ZERO
    years: Decimal = ZERO


@dataclass(frozen=True)
class PortfolioMetrics:
    """组合级指标（单实例时为基础值，多实例结构一致）。"""

    total_return_pct: Decimal = ZERO
    max_drawdown_pct: Decimal = ZERO
    sharpe: Decimal = ZERO
    correlation_matrix: dict[str, dict[str, Decimal]] = field(default_factory=dict)


def compute_instance_metrics(
    equity_curve: list[tuple[datetime, Decimal]],
    trades: list[dict[str, object]],
    initial_capital: Decimal,
    risk_budget_per_trade: Decimal | None = None,
) -> InstanceMetrics:
    """从权益曲线与成交流水计算实例级指标。"""
    if not equity_curve:
        return InstanceMetrics()

    equities = [Decimal(e) for _, e in equity_curve]
    final_equity = equities[-1]
    total_pnl = final_equity - Decimal(initial_capital)
    total_return = total_pnl / Decimal(initial_capital) if initial_capital else ZERO

    # 回撤
    peak = equities[0]
    max_dd = ZERO
    for e in equities:
        peak = max(peak, e)
        if peak > ZERO:
            dd = (peak - e) / peak
            max_dd = max(max_dd, dd)

    # 年化参数
    start_ts, end_ts = equity_curve[0][0], equity_curve[-1][0]
    days = max((end_ts - start_ts).total_seconds() / 86400, 1e-9)
    years = Decimal(str(days / 365.25))
    bars_per_year = Decimal(str(len(equities))) / years if years > ZERO else ZERO

    # 夏普（bar 收益率，无风险利率 0）
    returns = [
        (equities[i] - equities[i - 1]) / equities[i - 1]
        for i in range(1, len(equities))
        if equities[i - 1] > ZERO
    ]
    sharpe = ZERO
    if returns:
        mean = sum(returns) / Decimal(len(returns))
        var = sum((r - mean) ** 2 for r in returns) / Decimal(len(returns))
        std = Decimal(str(math.sqrt(float(var))))
        if std > ZERO and bars_per_year > ZERO:
            sharpe = mean / std * Decimal(str(math.sqrt(float(bars_per_year))))

    # 交易统计（平仓成交的毛盈亏）
    pnls = [
        Decimal(t["pnl"])  # type: ignore[arg-type]
        for t in trades
        if t.get("offset") == "CLOSE" and t.get("pnl") is not None
    ]
    wins = [p for p in pnls if p > ZERO]
    losses = [p for p in pnls if p < ZERO]
    round_trips = len(pnls)
    win_rate = (Decimal(len(wins)) / Decimal(round_trips)) if round_trips else ZERO
    gross_profit = sum(wins, ZERO)
    gross_loss = -sum(losses, ZERO)
    profit_factor = (gross_profit / gross_loss) if gross_loss > ZERO else ZERO
    avg_win = (gross_profit / Decimal(len(wins))) if wins else ZERO
    avg_loss = (gross_loss / Decimal(len(losses))) if losses else ZERO
    avg_loss_vs_budget = ZERO
    if risk_budget_per_trade and risk_budget_per_trade > ZERO and losses:
        avg_loss_vs_budget = avg_loss / Decimal(risk_budget_per_trade)

    return InstanceMetrics(
        total_pnl=total_pnl,
        total_return_pct=total_return * Decimal("100"),
        max_drawdown_pct=max_dd * Decimal("100"),
        sharpe=sharpe,
        win_rate_pct=win_rate * Decimal("100"),
        profit_factor=profit_factor,
        trade_count=len(trades),
        round_trips=round_trips,
        avg_win=avg_win,
        avg_loss=avg_loss,
        avg_loss_vs_budget_pct=avg_loss_vs_budget * Decimal("100"),
        bars_per_year=bars_per_year,
        years=years,
    )


def daily_returns(equity_curve: list[tuple[datetime, Decimal]]) -> dict[str, Decimal]:
    """按自然日聚合的日收益率（策略相关性矩阵用）。"""
    by_day: dict[str, Decimal] = {}
    last_of_day: dict[str, Decimal] = {}
    prev_close: Decimal | None = None
    for ts, equity in equity_curve:
        day = ts.date().isoformat()
        last_of_day[day] = Decimal(equity)
    for day in sorted(last_of_day):
        close = last_of_day[day]
        if prev_close is not None and prev_close > ZERO:
            by_day[day] = (close - prev_close) / prev_close
        prev_close = close
    return by_day


def correlation_matrix(returns_by_instance: dict[str, dict[str, Decimal]]) -> dict[str, dict[str, Decimal]]:
    """实例间日收益相关性矩阵（皮尔逊）。"""
    ids = sorted(returns_by_instance)
    matrix: dict[str, dict[str, Decimal]] = {}
    for a in ids:
        matrix[a] = {}
        for b in ids:
            common = sorted(set(returns_by_instance[a]) & set(returns_by_instance[b]))
            if len(common) < 3:
                matrix[a][b] = ZERO
                continue
            ra = [returns_by_instance[a][d] for d in common]
            rb = [returns_by_instance[b][d] for d in common]
            mean_a = sum(ra) / Decimal(len(ra))
            mean_b = sum(rb) / Decimal(len(rb))
            cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb, strict=True)) / Decimal(len(ra))
            std_a = Decimal(str(math.sqrt(float(sum((x - mean_a) ** 2 for x in ra) / len(ra)))))
            std_b = Decimal(str(math.sqrt(float(sum((y - mean_b) ** 2 for y in rb) / len(rb)))))
            matrix[a][b] = (cov / (std_a * std_b)) if std_a > ZERO and std_b > ZERO else ZERO
    return matrix
