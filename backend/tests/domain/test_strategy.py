"""Strategy / StrategyInstance 测试：版本链不变量与实例不变量。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from futures_trading.common.errors import InvariantViolationError
from futures_trading.common.types import Money
from futures_trading.trading.domain.model.signal import Direction
from futures_trading.trading.domain.model.strategy import (
    InstanceMode,
    ParameterSet,
    Strategy,
    StrategyInstance,
    StrategyType,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_parameter_set(version: int) -> ParameterSet:
    return ParameterSet(
        version=version,
        params={"fast_window": 5, "slow_window": 20, "atr_window": 20, "stop_multiplier": "2"},
        created_at=NOW,
    )


def make_strategy() -> Strategy:
    return Strategy(strategy_id="trend_atr_v1", type=StrategyType.TREND_ATR, universe=frozenset({"rb", "hc"}))


def make_instance(**overrides: object) -> StrategyInstance:
    defaults: dict[str, object] = {
        "instance_id": "inst-1",
        "strategy_id": "trend_atr_v1",
        "param_version": 1,
        "mode": InstanceMode.SHADOW,
        "capital_slice": Money(Decimal("100000")),
        "slice_peak": Money(Decimal("100000")),
    }
    defaults.update(overrides)
    return StrategyInstance(**defaults)  # type: ignore[arg-type]


class TestParameterSet:
    def test_params_immutable(self) -> None:
        parameter_set = make_parameter_set(1)
        with pytest.raises(TypeError):
            parameter_set.params["fast_window"] = 10  # type: ignore[index]

    def test_version_must_be_positive(self) -> None:
        with pytest.raises(InvariantViolationError):
            make_parameter_set(0)


class TestStrategy:
    def test_version_chain_monotonic(self) -> None:
        strategy = make_strategy()
        strategy.add_version(make_parameter_set(1))
        strategy.add_version(make_parameter_set(2))
        assert strategy.latest_version.version == 2

    def test_non_monotonic_version_rejected(self) -> None:
        strategy = make_strategy()
        strategy.add_version(make_parameter_set(2))
        with pytest.raises(InvariantViolationError, match="严格递增"):
            strategy.add_version(make_parameter_set(2))

    def test_latest_version_without_versions_rejected(self) -> None:
        with pytest.raises(InvariantViolationError, match="尚未定义任何参数版本"):
            _ = make_strategy().latest_version

    def test_version_of(self) -> None:
        strategy = make_strategy()
        strategy.add_version(make_parameter_set(1))
        strategy.add_version(make_parameter_set(2))
        assert strategy.version_of(1).version == 1
        with pytest.raises(InvariantViolationError, match="不存在版本"):
            strategy.version_of(9)

    def test_empty_universe_rejected(self) -> None:
        with pytest.raises(InvariantViolationError, match="universe 不能为空"):
            Strategy(strategy_id="s", type=StrategyType.TREND_ATR, universe=frozenset())


class TestStrategyInstance:
    def test_non_positive_slice_rejected(self) -> None:
        with pytest.raises(InvariantViolationError, match="资金切片必须为正"):
            make_instance(capital_slice=Money(Decimal("0")))

    def test_peak_below_slice_rejected(self) -> None:
        with pytest.raises(InvariantViolationError, match="slice_peak 不能小于"):
            make_instance(capital_slice=Money(Decimal("100000")), slice_peak=Money(Decimal("50000")))

    def test_position_for_get_or_create(self) -> None:
        instance = make_instance()
        position = instance.position_for("rb2510", Direction.LONG, Decimal("10"))
        assert instance.position_for("rb2510", Direction.LONG, Decimal("10")) is position

    def test_position_for_reverse_direction_rejected(self) -> None:
        instance = make_instance()
        instance.position_for("rb2510", Direction.LONG, Decimal("10"))
        with pytest.raises(InvariantViolationError, match="反向持仓"):
            instance.position_for("rb2510", Direction.SHORT, Decimal("10"))

    def test_update_slice_peak_only_upwards(self) -> None:
        instance = make_instance()
        instance.update_slice_peak(Money(Decimal("120000")))
        assert instance.slice_peak == Money(Decimal("120000"))
        instance.update_slice_peak(Money(Decimal("90000")))
        assert instance.slice_peak == Money(Decimal("120000"))

    def test_close_position_removes_flat(self) -> None:
        from futures_trading.common.types import Price, Quantity
        from futures_trading.trading.domain.model.position import Fill

        instance = make_instance()
        position = instance.position_for("rb2510", Direction.LONG, Decimal("10"))
        position.add(Fill(quantity=Quantity(Decimal("1")), price=Price(Decimal("3500"))))
        position.reduce(Fill(quantity=Quantity(Decimal("1")), price=Price(Decimal("3510"))))
        instance.close_position("rb2510")
        assert "rb2510" not in instance.positions
