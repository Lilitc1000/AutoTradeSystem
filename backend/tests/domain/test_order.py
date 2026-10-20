"""Order 聚合根测试：状态机、幂等、成交累计。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from futures_trading.common.types import ZERO, Price, Quantity
from futures_trading.trading.domain.errors import InvalidOrderTransitionError
from futures_trading.trading.domain.model.order import (
    Offset,
    Order,
    OrderStatus,
    PriceType,
    make_idempotency_key,
)
from futures_trading.trading.domain.model.signal import Direction


def make_order(**overrides: object) -> Order:
    defaults: dict[str, object] = {
        "order_id": "O-1",
        "idempotency_key": "key-1",
        "instance_id": "inst-1",
        "contract": "rb2510",
        "symbol": "rb",
        "direction": Direction.LONG,
        "offset": Offset.OPEN,
        "quantity": Quantity(Decimal("2")),
        "price_type": PriceType.LIMIT,
        "limit_price": Price(Decimal("3500")),
        "signal_id": "sig-1",
    }
    defaults.update(overrides)
    return Order(**defaults)  # type: ignore[arg-type]


class TestConstruction:
    def test_valid_limit_order(self) -> None:
        order = make_order()
        assert order.status is OrderStatus.PENDING
        assert order.remaining_quantity == Quantity(Decimal("2"))

    def test_zero_quantity_rejected(self) -> None:
        with pytest.raises(ValueError, match="quantity 必须为正"):
            make_order(quantity=Quantity(Decimal("0")))

    def test_limit_without_price_rejected(self) -> None:
        with pytest.raises(ValueError, match="限价单必须提供 limit_price"):
            make_order(limit_price=None)

    def test_market_order_without_price_ok(self) -> None:
        order = make_order(price_type=PriceType.MARKET, limit_price=None)
        assert order.price_type is PriceType.MARKET


class TestStateMachine:
    def test_happy_path(self) -> None:
        order = make_order()
        order.submit()
        assert order.status is OrderStatus.SUBMITTED
        order.partial_fill(Quantity(Decimal("1")), Price(Decimal("3500")))
        assert order.status is OrderStatus.PARTIAL
        order.partial_fill(Quantity(Decimal("1")), Price(Decimal("3501")))
        assert order.status is OrderStatus.FILLED

    def test_pending_fill_auto_submits_first(self) -> None:
        """成交回报先于委托确认到达（CTP 回报乱序）时，partial_fill 自动补
        SUBMITTED 迁移再进 FILLED——领域不变式：成交前必先提交。"""
        order = make_order()
        order.partial_fill(Quantity(Decimal("2")), Price(Decimal("3500")))
        assert order.status is OrderStatus.FILLED
        assert order.filled_quantity == Quantity(Decimal("2"))
        assert order.avg_fill_price == Price(Decimal("3500"))

    def test_final_state_is_terminal(self) -> None:
        order = make_order()
        order.submit()
        order.cancel()
        assert order.is_final
        with pytest.raises(InvalidOrderTransitionError):
            order.submit()

    def test_reject_sets_reason(self) -> None:
        order = make_order()
        order.reject("保证金不足")
        assert order.status is OrderStatus.REJECTED
        assert order.reject_reason == "保证金不足"

    def test_cancel_from_pending(self) -> None:
        order = make_order()
        order.cancel()
        assert order.status is OrderStatus.CANCELLED


class TestFills:
    def test_partial_fill_accumulates_weighted_average(self) -> None:
        order = make_order(quantity=Quantity(Decimal("3")))
        order.submit()
        order.partial_fill(Quantity(Decimal("1")), Price(Decimal("3500")))
        order.partial_fill(Quantity(Decimal("2")), Price(Decimal("3510")))
        assert order.filled_quantity == Quantity(Decimal("3"))
        # (1*3500 + 2*3510) / 3 = 3506.666...
        assert Decimal(order.avg_fill_price) == pytest.approx(Decimal("3506.666667"), abs=Decimal("0.001"))

    def test_fill_beyond_quantity_rejected(self) -> None:
        order = make_order(quantity=Quantity(Decimal("2")))
        order.submit()
        with pytest.raises(ValueError, match="超出订单量"):
            order.partial_fill(Quantity(Decimal("3")), Price(Decimal("3500")))

    def test_non_positive_fill_rejected(self) -> None:
        order = make_order()
        order.submit()
        with pytest.raises(ValueError, match="成交量必须为正"):
            order.partial_fill(Quantity(ZERO), Price(Decimal("3500")))


class TestIdempotencyKey:
    def test_deterministic(self) -> None:
        key1 = make_idempotency_key("i", "s", Direction.LONG, Offset.OPEN, "rb2510")
        key2 = make_idempotency_key("i", "s", Direction.LONG, Offset.OPEN, "rb2510")
        assert key1 == key2

    def test_differs_by_signal(self) -> None:
        key1 = make_idempotency_key("i", "s1", Direction.LONG, Offset.OPEN, "rb2510")
        key2 = make_idempotency_key("i", "s2", Direction.LONG, Offset.OPEN, "rb2510")
        assert key1 != key2

    def test_differs_by_offset(self) -> None:
        open_key = make_idempotency_key("i", "s", Direction.LONG, Offset.OPEN, "rb2510")
        close_key = make_idempotency_key("i", "s", Direction.LONG, Offset.CLOSE, "rb2510")
        assert open_key != close_key
