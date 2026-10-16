"""Portfolio 聚合根测试：组合层敞口计算与约束。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from futures_trading.common.errors import InvariantViolationError
from futures_trading.common.types import Money, Price, Quantity
from futures_trading.trading.domain.model.instrument import Exchange, Instrument
from futures_trading.trading.domain.model.portfolio import (
    Portfolio,
    PortfolioConstraints,
)
from futures_trading.trading.domain.model.position import Fill
from futures_trading.trading.domain.model.signal import Direction
from futures_trading.trading.domain.model.strategy import (
    InstanceMode,
    StrategyInstance,
)


def make_instrument(symbol: str, contract: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        contract=contract,
        name=contract,
        exchange=Exchange.SHFE,
        multiplier=Decimal("10"),
        tick_size=Decimal("1"),
        margin_rate=Decimal("0.12"),
    )


def make_instance(instance_id: str, slice_size: str = "100000") -> StrategyInstance:
    return StrategyInstance(
        instance_id=instance_id,
        strategy_id="trend_atr_v1",
        param_version=1,
        mode=InstanceMode.SHADOW,
        capital_slice=Money(Decimal(slice_size)),
        slice_peak=Money(Decimal(slice_size)),
    )


def add_position(instance: StrategyInstance, contract: str, direction: Direction, qty: str, price: str) -> None:
    position = instance.position_for(contract, direction, Decimal("10"))
    position.add(Fill(quantity=Quantity(Decimal(qty)), price=Price(Decimal(price))))


@pytest.fixture
def portfolio() -> Portfolio:
    return Portfolio(equity=Money(Decimal("1000000")), peak_equity=Money(Decimal("1000000")))


class TestConstraints:
    def test_defaults_valid(self) -> None:
        constraints = PortfolioConstraints()
        assert constraints.instrument_exposure_cap_pct == Decimal("0.20")

    def test_out_of_range_rejected(self) -> None:
        with pytest.raises(InvariantViolationError, match="必须在 \\(0, 1\\] 区间"):
            PortfolioConstraints(instrument_exposure_cap_pct=Decimal("0"))


class TestRegistration:
    def test_register_and_duplicate_rejected(self, portfolio: Portfolio) -> None:
        portfolio.register(make_instance("i1"))
        with pytest.raises(InvariantViolationError, match="已注册"):
            portfolio.register(make_instance("i1"))

    def test_unregister(self, portfolio: Portfolio) -> None:
        portfolio.register(make_instance("i1"))
        portfolio.unregister("i1")
        assert portfolio.instances == {}


class TestExposure:
    def test_instrument_exposure_sums_absolute(self, portfolio: Portfolio) -> None:
        i1 = make_instance("i1")
        i2 = make_instance("i2")
        add_position(i1, "rb2510", Direction.LONG, "2", "3500")  # 2*3500*10 = 70000
        add_position(i2, "rb2510", Direction.SHORT, "1", "3500")  # 1*3500*10 = 35000
        portfolio.register(i1)
        portfolio.register(i2)
        exposure = portfolio.instrument_exposure("rb", {"rb2510": Price(Decimal("3500"))})
        assert exposure == Money(Decimal("105000"))  # 多空绝对值之和

    def test_instrument_exposure_ignores_other_symbols(self, portfolio: Portfolio) -> None:
        i1 = make_instance("i1")
        add_position(i1, "rb2510", Direction.LONG, "1", "3500")
        add_position(i1, "hc2601", Direction.LONG, "1", "3000")
        portfolio.register(i1)
        assert portfolio.instrument_exposure("rb", {"rb2510": Price(Decimal("3500"))}) == Money(Decimal("35000"))

    def test_net_direction_long(self, portfolio: Portfolio) -> None:
        i1 = make_instance("i1")
        i2 = make_instance("i2")
        add_position(i1, "rb2510", Direction.LONG, "2", "3500")
        add_position(i2, "rb2510", Direction.SHORT, "1", "3500")
        portfolio.register(i1)
        portfolio.register(i2)
        assert portfolio.net_direction("rb", {"rb2510": Price(Decimal("3500"))}) is Direction.LONG

    def test_net_direction_balanced_returns_none(self, portfolio: Portfolio) -> None:
        i1 = make_instance("i1")
        i2 = make_instance("i2")
        add_position(i1, "rb2510", Direction.LONG, "1", "3500")
        add_position(i2, "rb2510", Direction.SHORT, "1", "3500")
        portfolio.register(i1)
        portfolio.register(i2)
        assert portfolio.net_direction("rb", {"rb2510": Price(Decimal("3500"))}) is None

    def test_same_direction_pct(self, portfolio: Portfolio) -> None:
        i1 = make_instance("i1")
        add_position(i1, "rb2510", Direction.LONG, "2", "3500")  # 70000 / 1000000 = 7%
        portfolio.register(i1)
        pct = portfolio.same_direction_exposure_pct({"rb2510": Price(Decimal("3500"))})
        assert pct == Decimal("0.07")


class TestDrawdown:
    def test_total_drawdown(self, portfolio: Portfolio) -> None:
        portfolio.equity = Money(Decimal("900000"))
        assert portfolio.total_drawdown() == Decimal("0.10")

    def test_update_peak_only_upwards(self, portfolio: Portfolio) -> None:
        portfolio.equity = Money(Decimal("1200000"))
        portfolio.update_peak()
        assert portfolio.peak_equity == Money(Decimal("1200000"))
        portfolio.equity = Money(Decimal("1100000"))
        portfolio.update_peak()
        assert portfolio.peak_equity == Money(Decimal("1200000"))

    def test_instance_drawdown(self, portfolio: Portfolio) -> None:
        i1 = make_instance("i1")
        i1.daily_pnl = Money(Decimal("-20000"))  # 峰值 100000 -> 当前 80000 -> 20%
        portfolio.register(i1)
        assert portfolio.instance_drawdown("i1") == Decimal("0.20")

    def test_instance_drawdown_unknown_instance_rejected(self, portfolio: Portfolio) -> None:
        with pytest.raises(InvariantViolationError, match="未注册"):
            portfolio.instance_drawdown("nope")


class TestAllocation:
    def test_total_allocated(self, portfolio: Portfolio) -> None:
        portfolio.register(make_instance("i1", "300000"))
        portfolio.register(make_instance("i2", "200000"))
        assert portfolio.total_allocated() == Money(Decimal("500000"))
        assert portfolio.allocation_ratio() == Decimal("0.5")

    def test_allocation_ratio_zero_equity(self) -> None:
        portfolio = Portfolio(equity=Money(Decimal("0")), peak_equity=Money(Decimal("0")))
        assert portfolio.allocation_ratio() == Decimal("0")
