"""指标计算测试：已知序列 → 已知指标。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from futures_trading.trading.application.metrics import (
    compute_instance_metrics,
    correlation_matrix,
    daily_returns,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def curve(values: list[float]) -> list[tuple[datetime, Decimal]]:
    return [(START + timedelta(days=i), Decimal(str(v))) for i, v in enumerate(values)]


class TestInstanceMetrics:
    def test_basic(self) -> None:
        metrics = compute_instance_metrics(
            curve([100, 110, 105, 120]),
            [],
            Decimal("100"),
        )
        assert metrics.total_pnl == Decimal("20")
        assert metrics.total_return_pct == Decimal("20")

    def test_max_drawdown(self) -> None:
        # 峰值 110 → 谷值 105 = 4.5454...%
        metrics = compute_instance_metrics(curve([100, 110, 105, 120]), [], Decimal("100"))
        assert metrics.max_drawdown_pct == Decimal("4.54545454545454509474685938") or (
            abs(metrics.max_drawdown_pct - Decimal("4.5454")) < Decimal("0.001")
        )

    def test_win_rate_and_profit_factor(self) -> None:
        trades = [
            {"offset": "CLOSE", "pnl": Decimal("10")},
            {"offset": "CLOSE", "pnl": Decimal("-5")},
            {"offset": "CLOSE", "pnl": Decimal("20")},
            {"offset": "OPEN", "pnl": None},
        ]
        metrics = compute_instance_metrics(curve([100, 110, 105, 120]), trades, Decimal("100"))
        assert metrics.round_trips == 3
        assert metrics.win_rate_pct == Decimal("66.66666666666666666666666667") or (
            abs(metrics.win_rate_pct - Decimal("66.67")) < Decimal("0.01")
        )
        assert metrics.profit_factor == Decimal("6")  # 30 / 5

    def test_avg_loss_vs_budget(self) -> None:
        trades = [
            {"offset": "CLOSE", "pnl": Decimal("-1500")},
            {"offset": "CLOSE", "pnl": Decimal("-500")},
        ]
        # 平均亏损 1000，预算 1000 → 100%
        metrics = compute_instance_metrics(
            curve([100, 90]), trades, Decimal("100"), risk_budget_per_trade=Decimal("1000")
        )
        assert metrics.avg_loss_vs_budget_pct == Decimal("100")

    def test_empty_curve(self) -> None:
        metrics = compute_instance_metrics([], [], Decimal("100"))
        assert metrics.total_pnl == Decimal("0")


class TestCorrelation:
    def test_daily_returns_aggregation(self) -> None:
        result = daily_returns(
            [
                (datetime(2026, 1, 1, 10, tzinfo=UTC), Decimal("100")),
                (datetime(2026, 1, 1, 15, tzinfo=UTC), Decimal("110")),
                (datetime(2026, 1, 2, 10, tzinfo=UTC), Decimal("121")),
            ]
        )
        assert result["2026-01-02"] == Decimal("0.1")

    def test_perfect_correlation(self) -> None:
        r = {str(d): Decimal(d) for d in range(1, 6)}
        matrix = correlation_matrix({"a": r, "b": r})
        assert abs(matrix["a"]["b"] - Decimal("1")) < Decimal("0.0001")

    def test_insufficient_data_zero(self) -> None:
        matrix = correlation_matrix({"a": {"d1": Decimal("0.1")}, "b": {"d1": Decimal("0.2")}})
        assert matrix["a"]["b"] == Decimal("0")
