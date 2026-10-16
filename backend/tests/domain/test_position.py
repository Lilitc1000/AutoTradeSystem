"""Position 实体测试：加权均价、平仓盈亏、不变量。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from futures_trading.common.types import Money, Price, Quantity
from futures_trading.trading.domain.errors import InsufficientPositionError, InvalidFillError
from futures_trading.trading.domain.model.position import Fill, Position
from futures_trading.trading.domain.model.signal import Direction


def make_position(direction: Direction = Direction.LONG) -> Position:
    return Position(
        instance_id="inst-1",
        contract="rb2510",
        direction=direction,
        multiplier=Decimal("10"),
    )


class TestAdd:
    def test_first_fill_sets_price(self) -> None:
        position = make_position()
        position.add(Fill(quantity=Quantity(Decimal("2")), price=Price(Decimal("3500"))))
        assert position.quantity == Quantity(Decimal("2"))
        assert position.avg_price == Price(Decimal("3500"))

    def test_weighted_average(self) -> None:
        position = make_position()
        position.add(Fill(quantity=Quantity(Decimal("2")), price=Price(Decimal("3500"))))
        position.add(Fill(quantity=Quantity(Decimal("2")), price=Price(Decimal("3520"))))
        assert position.quantity == Quantity(Decimal("4"))
        assert position.avg_price == Price(Decimal("3510"))

    def test_non_positive_fill_rejected(self) -> None:
        position = make_position()
        with pytest.raises(InvalidFillError):
            position.add(Fill(quantity=Quantity(Decimal("0")), price=Price(Decimal("3500"))))


class TestReduce:
    def test_long_close_profit(self) -> None:
        position = make_position(Direction.LONG)
        position.add(Fill(quantity=Quantity(Decimal("2")), price=Price(Decimal("3500"))))
        realized = position.reduce(Fill(quantity=Quantity(Decimal("2")), price=Price(Decimal("3520"))))
        # (3520-3500) * 2 手 * 乘数 10 = 400
        assert realized == Money(Decimal("400"))
        assert position.is_flat()
        assert position.avg_price is None

    def test_short_close_profit(self) -> None:
        position = make_position(Direction.SHORT)
        position.add(Fill(quantity=Quantity(Decimal("1")), price=Price(Decimal("3500"))))
        realized = position.reduce(Fill(quantity=Quantity(Decimal("1")), price=Price(Decimal("3480"))))
        # (3500-3480) * 1 * 10 = 200
        assert realized == Money(Decimal("200"))

    def test_close_beyond_holding_rejected(self) -> None:
        position = make_position()
        position.add(Fill(quantity=Quantity(Decimal("1")), price=Price(Decimal("3500"))))
        with pytest.raises(InsufficientPositionError, match="超过持仓"):
            position.reduce(Fill(quantity=Quantity(Decimal("2")), price=Price(Decimal("3500"))))

    def test_partial_close_keeps_position(self) -> None:
        position = make_position()
        position.add(Fill(quantity=Quantity(Decimal("3")), price=Price(Decimal("3500"))))
        position.reduce(Fill(quantity=Quantity(Decimal("1")), price=Price(Decimal("3510"))))
        assert position.quantity == Quantity(Decimal("2"))
        assert not position.is_flat()


class TestUnrealizedPnl:
    def test_long_floating_profit(self) -> None:
        position = make_position(Direction.LONG)
        position.add(Fill(quantity=Quantity(Decimal("1")), price=Price(Decimal("3500"))))
        assert position.unrealized_pnl(Price(Decimal("3530"))) == Money(Decimal("300"))

    def test_short_floating_profit(self) -> None:
        position = make_position(Direction.SHORT)
        position.add(Fill(quantity=Quantity(Decimal("1")), price=Price(Decimal("3500"))))
        assert position.unrealized_pnl(Price(Decimal("3470"))) == Money(Decimal("300"))

    def test_flat_returns_zero(self) -> None:
        position = make_position()
        assert position.unrealized_pnl(Price(Decimal("3500"))) == Money(Decimal("0"))
