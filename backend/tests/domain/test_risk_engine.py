"""RiskEngine 测试：每条硬规则、优先级与 fail-closed。"""

from __future__ import annotations

from decimal import Decimal

from futures_trading.common.types import Money
from futures_trading.trading.domain.service.risk_engine import RiskEngine, RiskLimits, RiskSnapshot


def healthy_snapshot(**overrides: object) -> RiskSnapshot:
    defaults: dict[str, object] = {
        "equity": Money(Decimal("1000000")),
        "peak_equity": Money(Decimal("1000000")),
        "slice_equity": Money(Decimal("100000")),
        "slice_peak": Money(Decimal("100000")),
        "daily_pnl": Money(Decimal("0")),
        "instruments_reconciled": True,
        "market_data_fresh": True,
        "api_healthy": True,
        "margin_sufficient": True,
        "order_has_stop_loss": True,
    }
    defaults.update(overrides)
    return RiskSnapshot(**defaults)  # type: ignore[arg-type]


class TestAllows:
    def test_healthy_open_allowed(self) -> None:
        decision = RiskEngine().check(healthy_snapshot(), is_open=True)
        assert decision.allowed
        assert decision.rule is None

    def test_healthy_close_allowed(self) -> None:
        decision = RiskEngine().check(healthy_snapshot(), is_open=False)
        assert decision.allowed


class TestSystemRules:
    def test_unreconciled_rejected(self) -> None:
        decision = RiskEngine().check(healthy_snapshot(instruments_reconciled=False), is_open=True)
        assert not decision.allowed
        assert decision.rule == "R01_RECONCILE_PENDING"

    def test_stale_market_data_rejected(self) -> None:
        decision = RiskEngine().check(healthy_snapshot(market_data_fresh=False), is_open=True)
        assert not decision.allowed
        assert decision.rule == "R02_STALE_MARKET_DATA"

    def test_unhealthy_api_rejected(self) -> None:
        decision = RiskEngine().check(healthy_snapshot(api_healthy=False), is_open=True)
        assert not decision.allowed
        assert decision.rule == "R03_API_UNHEALTHY"

    def test_insufficient_margin_rejected(self) -> None:
        decision = RiskEngine().check(healthy_snapshot(margin_sufficient=False), is_open=True)
        assert not decision.allowed
        assert decision.rule == "R04_MARGIN_INSUFFICIENT"


class TestDrawdownRules:
    def test_portfolio_drawdown_breaker(self) -> None:
        # 组合从 1000000 跌到 890000 -> 11% > 10%
        decision = RiskEngine().check(healthy_snapshot(equity=Money(Decimal("890000"))), is_open=True)
        assert not decision.allowed
        assert decision.rule == "R05_PORTFOLIO_DRAWDOWN"

    def test_portfolio_drawdown_below_limit_ok(self) -> None:
        decision = RiskEngine().check(healthy_snapshot(equity=Money(Decimal("950000"))), is_open=True)
        assert decision.allowed

    def test_slice_drawdown_breaker(self) -> None:
        # 切片从 100000 跌到 84000 -> 16% > 15%
        decision = RiskEngine().check(healthy_snapshot(slice_equity=Money(Decimal("84000"))), is_open=True)
        assert not decision.allowed
        assert decision.rule == "R06_SLICE_DRAWDOWN"


class TestOpenOnlyRules:
    def test_daily_loss_blocks_open_but_allows_close(self) -> None:
        snapshot = healthy_snapshot(daily_pnl=Money(Decimal("-3000")))  # -3% > -2%
        open_decision = RiskEngine().check(snapshot, is_open=True)
        assert not open_decision.allowed
        assert open_decision.rule == "R07_DAILY_LOSS_LIMIT"
        close_decision = RiskEngine().check(snapshot, is_open=False)
        assert close_decision.allowed

    def test_no_stop_loss_rejected_on_open(self) -> None:
        decision = RiskEngine().check(healthy_snapshot(order_has_stop_loss=False), is_open=True)
        assert not decision.allowed
        assert decision.rule == "R08_NO_STOP_LOSS"

    def test_no_stop_loss_ignored_on_close(self) -> None:
        decision = RiskEngine().check(healthy_snapshot(order_has_stop_loss=False), is_open=False)
        assert decision.allowed


class TestPriorityAndFailClosed:
    def test_first_matching_rule_wins(self) -> None:
        # 多个问题并存：对账不平 + 行情停滞 -> 返回 R01
        snapshot = healthy_snapshot(instruments_reconciled=False, market_data_fresh=False)
        decision = RiskEngine().check(snapshot, is_open=True)
        assert decision.rule == "R01_RECONCILE_PENDING"

    def test_all_unknown_rejected(self) -> None:
        snapshot = healthy_snapshot(
            instruments_reconciled=False,
            market_data_fresh=False,
            api_healthy=False,
            margin_sufficient=False,
        )
        decision = RiskEngine().check(snapshot, is_open=False)
        assert not decision.allowed

    def test_custom_limits(self) -> None:
        engine = RiskEngine(RiskLimits(daily_loss_limit_pct=Decimal("0.05")))
        snapshot = healthy_snapshot(daily_pnl=Money(Decimal("-3000")))  # -3% < -5% 不触发
        assert engine.check(snapshot, is_open=True).allowed
