"""回测报告与高级 API（runner）。

runner 是回测的统一入口：组装行情/时钟/撮合器/引擎 → 跑 → 指标 → 报告。
前端（M3）与参数扫描都通过它调用。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal

from ...common.types import Money
from ..infrastructure.backtest.backtest_broker import BacktestBroker
from ..infrastructure.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from ..infrastructure.backtest.market_data import BacktestMarketData
from ..infrastructure.backtest.slippage_model import SlippageModel
from .metrics import InstanceMetrics, compute_instance_metrics


@dataclass(frozen=True)
class BacktestReport:
    """完整回测报告（JSON 可序列化）。"""

    run_id: str
    config: dict[str, object]
    metrics: dict[str, object]
    equity_curve: list[dict[str, object]] = field(default_factory=list)
    trades: list[dict[str, object]] = field(default_factory=list)
    signal_stats: dict[str, int] = field(default_factory=dict)
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "config": self.config,
            "metrics": self.metrics,
            "equity_curve": self.equity_curve,
            "trades": self.trades,
            "signal_stats": self.signal_stats,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def run_backtest(
    config: BacktestConfig,
    bars_by_symbol: dict[str, list],
    instruments: dict,
    run_id: str = "bt-run",
) -> BacktestReport:
    """执行一次回测并产出报告。

    bars_by_symbol: symbol -> 按时间升序的 Bar 列表（见 application.dtos.Bar）
    instruments: symbol -> Instrument
    """
    market_data = BacktestMarketData(bars_by_symbol)
    broker = BacktestBroker(
        initial_capital=Money(config.initial_capital),
        slippage=SlippageModel(
            ticks=config.slippage_ticks,
            commission_per_lot=Money(config.commission_per_lot),
        ),
        instruments=instruments,
    )
    engine = BacktestEngine(config, market_data, broker, instruments)
    result: BacktestResult = engine.run()

    # 单笔风险预算 = 初始资金 × 单笔风险比例（用于 avg_loss_vs_budget）
    risk_budget = Decimal(config.initial_capital) * Decimal(config.risk_per_trade_pct)
    metrics: InstanceMetrics = compute_instance_metrics(
        result.equity_curve,
        result.trades,
        Decimal(config.initial_capital),
        risk_budget_per_trade=risk_budget,
    )

    signal_stats: dict[str, int] = {}
    for outcome in result.outcomes:
        signal_stats[outcome.status] = signal_stats.get(outcome.status, 0) + 1

    return BacktestReport(
        run_id=run_id,
        config={
            "strategy_type": str(config.strategy_type),
            "param_version": config.param_version,
            "universe": list(config.universe),
            "params": dict(config.params),
            "initial_capital": str(config.initial_capital),
            "risk_per_trade_pct": str(config.risk_per_trade_pct),
            "stop_multiplier": str(config.stop_multiplier),
            "slippage_ticks": str(config.slippage_ticks),
            "commission_per_lot": str(config.commission_per_lot),
        },
        metrics={
            "total_pnl": str(metrics.total_pnl),
            "total_return_pct": str(round(metrics.total_return_pct, 4)),
            "max_drawdown_pct": str(round(metrics.max_drawdown_pct, 4)),
            "sharpe": str(round(metrics.sharpe, 4)),
            "win_rate_pct": str(round(metrics.win_rate_pct, 2)),
            "profit_factor": str(round(metrics.profit_factor, 4)),
            "trade_count": metrics.trade_count,
            "round_trips": metrics.round_trips,
            "avg_win": str(round(metrics.avg_win, 4)),
            "avg_loss": str(round(metrics.avg_loss, 4)),
            "avg_loss_vs_budget_pct": str(round(metrics.avg_loss_vs_budget_pct, 2)),
            "years": str(round(metrics.years, 3)),
        },
        equity_curve=[
            {"ts": ts.isoformat(), "equity": str(e)} for ts, e in result.equity_curve
        ],
        trades=[
            {k: _json_safe(v) for k, v in t.items()}
            for t in result.trades
        ],
        signal_stats=signal_stats,
    )


def _json_safe(value: object) -> object:
    """Decimal/datetime → JSON/PG-JSONB 可序列化形式。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


__all__ = ["BacktestReport", "run_backtest", "asdict"]
