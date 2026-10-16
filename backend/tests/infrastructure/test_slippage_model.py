"""SlippageModel 测试：滑点方向与手续费。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from futures_trading.common.types import Money, Price
from futures_trading.trading.domain.model.signal import Direction
from futures_trading.trading.infrastructure.backtest.slippage_model import SlippageModel


class TestExecutionPrice:
    def test_long_pays_more(self) -> None:
        model = SlippageModel(ticks=Decimal("2"))
        price = model.execution_price(Price(Decimal("3500")), Direction.LONG, Decimal("1"))
        assert price == Price(Decimal("3502"))

    def test_short_receives_less(self) -> None:
        model = SlippageModel(ticks=Decimal("2"))
        price = model.execution_price(Price(Decimal("3500")), Direction.SHORT, Decimal("1"))
        assert price == Price(Decimal("3498"))

    def test_fractional_tick(self) -> None:
        model = SlippageModel(ticks=Decimal("0.5"))
        price = model.execution_price(Price(Decimal("3500")), Direction.LONG, Decimal("2"))
        assert price == Price(Decimal("3501"))


class TestCommission:
    def test_per_lot(self) -> None:
        model = SlippageModel(commission_per_lot=Money(Decimal("12.5")))
        assert model.commission(4) == Money(Decimal("50"))

    def test_zero_lots(self) -> None:
        assert SlippageModel().commission(0) == Money(Decimal("0"))

    def test_negative_lots_rejected(self) -> None:
        with pytest.raises(ValueError, match="lots 不能为负"):
            SlippageModel().commission(-1)
