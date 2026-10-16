"""runner 端到端 + scanner 测试。"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from futures_trading.common.instrument_registry import get_instrument
from futures_trading.trading.application.dtos import Bar
from futures_trading.trading.application.runner import run_backtest
from futures_trading.trading.domain.model.strategy import StrategyType
from futures_trading.trading.infrastructure.backtest.engine import BacktestConfig
from futures_trading.trading.infrastructure.scanner import grid_scan

START = datetime(2026, 1, 5, tzinfo=UTC)


def synthesize(symbol: str, count: int = 300, seed: int = 42) -> list[Bar]:
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = Decimal("3500")
    ts = START
    for i in range(count):
        drift = Decimal("1.2") if 80 <= i < 200 else Decimal("-0.4")
        change = Decimal(str(rng.gauss(float(drift), 8.0)))
        open_price = price
        close = max(Decimal("1"), open_price + change)
        bars.append(
            Bar(
                symbol=symbol,
                interval_minutes=1440,
                ts=ts,
                open=open_price,
                high=max(open_price, close) + Decimal("2"),
                low=min(open_price, close) - Decimal("2"),
                close=close,
                volume=Decimal("2000"),
            )
        )
        price = close
        ts += timedelta(days=1)
    return bars


@pytest.fixture
def bars() -> dict[str, list[Bar]]:
    return {"rb": synthesize("rb")}


@pytest.fixture
def instruments() -> dict[str, object]:
    return {"rb": get_instrument("rb")}


def make_config(**params: object) -> BacktestConfig:
    base = {"fast_window": 5, "slow_window": 20, "atr_window": 20}
    base.update(params)
    return BacktestConfig(
        strategy_type=StrategyType.TREND_ATR,
        param_version=1,
        universe=("rb",),
        params=base,
        initial_capital=Decimal("1000000"),
        risk_per_trade_pct=Decimal("0.01"),
    )


class TestRunner:
    def test_report_structure(self, bars: dict[str, list[Bar]], instruments: dict[str, object]) -> None:
        report = run_backtest(make_config(), bars, instruments, run_id="r1")
        data = report.to_dict()
        assert data["run_id"] == "r1"
        assert "total_return_pct" in data["metrics"]
        assert "avg_loss_vs_budget_pct" in data["metrics"]  # 设计文档 7.4 王牌指标
        assert len(data["equity_curve"]) == len(bars["rb"])
        assert isinstance(data["signal_stats"], dict)

    def test_metrics_keys_complete(self, bars: dict[str, list[Bar]], instruments: dict[str, object]) -> None:
        report = run_backtest(make_config(), bars, instruments, run_id="r2")
        expected = {
            "total_pnl", "total_return_pct", "max_drawdown_pct", "sharpe",
            "win_rate_pct", "profit_factor", "trade_count", "round_trips",
            "avg_loss_vs_budget_pct", "years",
        }
        assert expected <= set(report.metrics)

    def test_different_params_different_result(
        self, bars: dict[str, list[Bar]], instruments: dict[str, object]
    ) -> None:
        a = run_backtest(make_config(fast_window=5, slow_window=20), bars, instruments, run_id="a")
        b = run_backtest(make_config(fast_window=10, slow_window=40), bars, instruments, run_id="b")
        assert (
            a.metrics["total_return_pct"] != b.metrics["total_return_pct"]
        )


class TestScanner:
    def test_grid_runs_all_combos(self, bars: dict[str, list[Bar]], instruments: dict[str, object]) -> None:
        results = grid_scan(
            make_config(),
            bars,
            instruments,
            {"fast_window": [3, 5], "slow_window": [20, 30]},
        )
        assert len(results) == 4
        # 按夏普降序
        sharpes = [Decimal(r.sharpe) for r in results]
        assert sharpes == sorted(sharpes, reverse=True)

    def test_too_many_params_rejected(self, bars: dict[str, list[Bar]], instruments: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="超过上限"):
            grid_scan(
                make_config(),
                bars,
                instruments,
                {"fast_window": [3, 5], "slow_window": [20, 30], "atr_window": [10, 20]},
            )

    def test_empty_grid_rejected(self, bars: dict[str, list[Bar]], instruments: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            grid_scan(make_config(), bars, instruments, {})
