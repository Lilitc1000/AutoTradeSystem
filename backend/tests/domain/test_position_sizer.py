"""PositionSizer 测试：四层取小与边界。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from futures_trading.common.types import Money, Price
from futures_trading.trading.domain.service.position_sizer import (
    SizingConstraints,
    SizingInput,
    calculate,
)

BASE_CONSTRAINTS = SizingConstraints(
    instrument_exposure_cap=Money(Decimal("200000")),
    current_instrument_exposure=Money(Decimal("0")),
    available_margin=Money(Decimal("500000")),
    margin_usage_cap_pct=Decimal("0.5"),
    strategy_slice_remaining=Money(Decimal("100000")),
    min_lots=1,
)


def make_input(**overrides: object) -> SizingInput:
    defaults: dict[str, object] = {
        "capital": Money(Decimal("100000")),
        "risk_per_trade_pct": Decimal("0.01"),
        "atr": Price(Decimal("50")),
        "stop_multiplier": Decimal("2"),
        "multiplier": Decimal("10"),
        "tick_size": Decimal("1"),
        "price": Price(Decimal("3500")),
        "margin_rate": Decimal("0.12"),
        "constraints": BASE_CONSTRAINTS,
    }
    defaults.update(overrides)
    return SizingInput(**defaults)  # type: ignore[arg-type]


class TestLayer1:
    def test_l1_binding_when_others_roomy(self) -> None:
        # risk_budget = 100000*1% = 1000
        # stop_distance = 50*2 = 100; risk_per_lot = 100*10 = 1000
        # L1 = floor(1000/1000) = 1
        result = calculate(make_input())
        assert result.lots == 1
        assert result.binding_layer == "L1"
        assert result.risk_budget == Money(Decimal("1000"))
        assert result.risk_per_lot == Money(Decimal("1000"))

    def test_risk_budget_halves_with_pct(self) -> None:
        result = calculate(make_input(risk_per_trade_pct=Decimal("0.005")))
        # 500/1000 = 0.5 -> floor 0 -> 不足最小手数
        assert result.lots == 0
        assert result.binding_layer is None

    def test_higher_atr_reduces_lots(self) -> None:
        result = calculate(make_input(atr=Price(Decimal("25"))))
        # stop_distance = 50; risk_per_lot = 500; L1 = 1000/500 = 2
        assert result.lots == 2
        assert result.binding_layer == "L1"


class TestLayer2:
    def test_l2_binding_when_instrument_nearly_full(self) -> None:
        # 每手保证金 = 3500*10*0.12 = 4200
        # 品种上限 200000，已用 195000 -> 余 5000 -> L2 = floor(5000/4200) = 1
        # L1 = floor(100000*5%/1000) = 5（充裕，不干扰 L2）
        constraints = SizingConstraints(
            instrument_exposure_cap=Money(Decimal("200000")),
            current_instrument_exposure=Money(Decimal("195000")),
            available_margin=Money(Decimal("500000")),
            margin_usage_cap_pct=Decimal("0.5"),
            strategy_slice_remaining=Money(Decimal("100000")),
            min_lots=1,
        )
        result = calculate(make_input(risk_per_trade_pct=Decimal("0.05"), constraints=constraints))
        assert result.lots == 1
        assert result.binding_layer == "L2"

    def test_l2_exhausted_gives_zero(self) -> None:
        constraints = SizingConstraints(
            instrument_exposure_cap=Money(Decimal("200000")),
            current_instrument_exposure=Money(Decimal("200000")),
            available_margin=Money(Decimal("500000")),
            margin_usage_cap_pct=Decimal("0.5"),
            strategy_slice_remaining=Money(Decimal("100000")),
            min_lots=1,
        )
        result = calculate(make_input(constraints=constraints))
        assert result.lots == 0
        assert result.binding_layer is None


class TestLayer3:
    def test_l3_binding_on_margin(self) -> None:
        # 可用保证金 20000 * 50% = 10000 -> L3 = floor(10000/4200) = 2
        # L1 = 5（充裕）
        constraints = SizingConstraints(
            instrument_exposure_cap=Money(Decimal("200000")),
            current_instrument_exposure=Money(Decimal("0")),
            available_margin=Money(Decimal("20000")),
            margin_usage_cap_pct=Decimal("0.5"),
            strategy_slice_remaining=Money(Decimal("100000")),
            min_lots=1,
        )
        result = calculate(make_input(risk_per_trade_pct=Decimal("0.05"), constraints=constraints))
        assert result.lots == 2
        assert result.binding_layer == "L3"


class TestLayer4:
    def test_l4_binding_on_slice(self) -> None:
        # 切片剩余 5000 -> L4 = floor(5000/4200) = 1
        # L1 = 5（充裕）
        constraints = SizingConstraints(
            instrument_exposure_cap=Money(Decimal("200000")),
            current_instrument_exposure=Money(Decimal("0")),
            available_margin=Money(Decimal("500000")),
            margin_usage_cap_pct=Decimal("0.5"),
            strategy_slice_remaining=Money(Decimal("5000")),
            min_lots=1,
        )
        result = calculate(make_input(risk_per_trade_pct=Decimal("0.05"), constraints=constraints))
        assert result.lots == 1
        assert result.binding_layer == "L4"


class TestValidation:
    def test_non_positive_atr_rejected(self) -> None:
        with pytest.raises(Exception, match="atr 必须为正"):
            calculate(make_input(atr=Price(Decimal("0"))))

    def test_risk_pct_out_of_range_rejected(self) -> None:
        with pytest.raises(Exception, match="risk_per_trade_pct"):
            calculate(make_input(risk_per_trade_pct=Decimal("1.5")))

    def test_min_lots_two_requires_two(self) -> None:
        constraints = SizingConstraints(
            instrument_exposure_cap=Money(Decimal("200000")),
            current_instrument_exposure=Money(Decimal("0")),
            available_margin=Money(Decimal("500000")),
            margin_usage_cap_pct=Decimal("0.5"),
            strategy_slice_remaining=Money(Decimal("100000")),
            min_lots=2,
        )
        result = calculate(make_input(constraints=constraints))  # L1 只有 1 手
        assert result.lots == 0
