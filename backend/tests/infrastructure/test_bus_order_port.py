"""M3-e2b 测试：总线报单端口（指令入队/发布/协议转换）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from futures_trading.common.types import Price, Quantity
from futures_trading.trading.domain.model.order import Direction, Offset, OrderStatus, PriceType
from futures_trading.trading.infrastructure.messaging.bus_order_port import (
    BusOrderPort,
    command_to_payload,
)


def _command(**overrides: object):
    from futures_trading.trading.application.dtos import OrderCommand

    base = {
        "instance_id": "i1",
        "contract": "rb2701",
        "symbol": "rb",
        "direction": Direction.LONG,
        "offset": Offset.OPEN,
        "quantity": Quantity(Decimal("3")),
        "price_type": PriceType.LIMIT,
        "limit_price": Price(Decimal("3067")),
        "signal_id": "s-1",
    }
    base.update(overrides)
    return OrderCommand(**base)  # type: ignore[arg-type]


class TestBusOrderPort:
    def test_submit_queues_and_returns_pending_order(self) -> None:
        port = BusOrderPort()
        order = port.submit(_command())
        assert order.order_id.startswith("BUS-")
        assert order.status is OrderStatus.SUBMITTED
        assert order.contract == "rb2701"
        assert port.pending_count == 1

    def test_drain_returns_batch_and_clears(self) -> None:
        port = BusOrderPort()
        port.submit(_command())
        port.submit(_command(contract="rb2702"))
        batch = port.drain()
        assert len(batch) == 2
        assert port.pending_count == 0
        assert port.drain() == []

    def test_order_ids_are_unique(self) -> None:
        port = BusOrderPort()
        a = port.submit(_command())
        b = port.submit(_command())
        assert a.order_id != b.order_id

    def test_query_returns_none_and_cancel_unsupported(self) -> None:
        """下单后状态在 trade_gateway 侧——本地不持订单状态。"""
        port = BusOrderPort()
        port.submit(_command())
        assert port.query("BUS-000001") is None
        with pytest.raises(NotImplementedError, match="trade_gateway"):
            port.cancel("BUS-000001")


class TestCommandPayload:
    def test_roundtrip_matches_bus_protocol(self) -> None:
        payload = command_to_payload(_command())
        assert payload == {
            "instance_id": "i1",
            "contract": "rb2701",
            "symbol": "rb",
            "direction": "LONG",
            "offset": "OPEN",
            "quantity": "3",
            "price_type": "LIMIT",
            "limit_price": "3067",
            "signal_id": "s-1",
        }

    def test_market_order_has_no_limit_price(self) -> None:
        payload = command_to_payload(_command(price_type=PriceType.MARKET, limit_price=None))
        assert payload["price_type"] == "MARKET"
        assert payload["limit_price"] is None

    def test_close_today_survives(self) -> None:
        """SHFE 平今标志必须能传到出口（M2 平仓拆分的关键字段）。"""
        payload = command_to_payload(_command(offset=Offset.CLOSE_TODAY))
        assert payload["offset"] == "CLOSE_TODAY"
