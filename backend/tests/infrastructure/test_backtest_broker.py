"""BacktestBroker 测试：撮合规则、滑点方向、幂等、记账。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from futures_trading.common.types import Money, Price, Quantity
from futures_trading.trading.application.dtos import Bar, OrderCommand
from futures_trading.trading.domain.model.instrument import Exchange, Instrument
from futures_trading.trading.domain.model.order import Offset, OrderStatus, PriceType
from futures_trading.trading.domain.model.signal import Direction
from futures_trading.trading.infrastructure.backtest.backtest_broker import BacktestBroker
from futures_trading.trading.infrastructure.backtest.slippage_model import SlippageModel

TS = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


@pytest.fixture
def instrument() -> Instrument:
    return Instrument(
        symbol="rb",
        contract="rb2510",
        name="螺纹钢",
        exchange=Exchange.SHFE,
        multiplier=Decimal("10"),
        tick_size=Decimal("1"),
        margin_rate=Decimal("0.12"),
    )


@pytest.fixture
def broker(instrument: Instrument) -> BacktestBroker:
    return BacktestBroker(
        initial_capital=Money(Decimal("100000")),
        slippage=SlippageModel(ticks=Decimal("1"), commission_per_lot=Money(Decimal("10"))),
        instruments={instrument.contract: instrument},
    )


def make_bar(open_price: str = "3500", high: str = "3510", low: str = "3490", close: str = "3505") -> Bar:
    return Bar(
        symbol="rb",
        interval_minutes=15,
        ts=TS,
        open=Price(Decimal(open_price)),
        high=Price(Decimal(high)),
        low=Price(Decimal(low)),
        close=Price(Decimal(close)),
        volume=Quantity(Decimal("1000")),
    )


def market_command(direction: Direction = Direction.LONG, offset: Offset = Offset.OPEN, qty: str = "1") -> OrderCommand:
    return OrderCommand(
        instance_id="inst-1",
        contract="rb2510",
        symbol="rb",
        direction=direction,
        offset=offset,
        quantity=Quantity(Decimal(qty)),
        price_type=PriceType.MARKET,
        signal_id="sig-1",
    )


def limit_command(limit: str, direction: Direction = Direction.LONG, offset: Offset = Offset.OPEN) -> OrderCommand:
    return OrderCommand(
        instance_id="inst-1",
        contract="rb2510",
        symbol="rb",
        direction=direction,
        offset=offset,
        quantity=Quantity(Decimal("1")),
        price_type=PriceType.LIMIT,
        limit_price=Price(Decimal(limit)),
        signal_id="sig-1",
    )


class TestMarketOrder:
    def test_long_fills_at_open_plus_tick(self, broker: BacktestBroker) -> None:
        order = broker.submit(market_command(Direction.LONG))
        assert order.status is OrderStatus.SUBMITTED  # 本地撮合器：挂单即送达
        filled = broker.on_bar(make_bar(open_price="3500"))
        assert len(filled) == 1
        # 市价买入：开盘价 + 1 个 tick = 3501
        assert filled[0].avg_fill_price == Price(Decimal("3501"))
        assert filled[0].status is OrderStatus.FILLED

    def test_short_fills_at_open_minus_tick(self, broker: BacktestBroker) -> None:
        broker.submit(market_command(Direction.SHORT))
        filled = broker.on_bar(make_bar(open_price="3500"))
        assert filled[0].avg_fill_price == Price(Decimal("3499"))

    def test_commission_deducted(self, broker: BacktestBroker) -> None:
        broker.submit(market_command(Direction.LONG, qty="2"))
        broker.on_bar(make_bar())
        # 2 手 * 10 元/手 = 20 手续费
        assert broker.balance == Money(Decimal("99980"))


class TestLimitOrder:
    def test_buy_limit_fills_when_low_touches(self, broker: BacktestBroker) -> None:
        broker.submit(limit_command("3495", Direction.LONG))
        filled = broker.on_bar(make_bar(low="3490", high="3510"))
        assert len(filled) == 1
        assert filled[0].avg_fill_price == Price(Decimal("3495"))

    def test_buy_limit_not_filled_when_price_stays_above(self, broker: BacktestBroker) -> None:
        broker.submit(limit_command("3480", Direction.LONG))
        filled = broker.on_bar(make_bar(low="3490"))
        assert filled == []
        assert broker.query("BT-000001").status is OrderStatus.SUBMITTED

    def test_sell_limit_fills_when_high_touches(self, broker: BacktestBroker) -> None:
        broker.submit(limit_command("3510", Direction.SHORT))
        filled = broker.on_bar(make_bar(high="3515", low="3490"))
        assert len(filled) == 1
        assert filled[0].avg_fill_price == Price(Decimal("3510"))


class TestAccounting:
    def test_close_realizes_pnl(self, broker: BacktestBroker) -> None:
        broker.submit(market_command(Direction.LONG, qty="1"))
        broker.on_bar(make_bar(open_price="3500"))
        # 3501 开仓；3521 平仓（+1 tick 滑点不利卖方 -> 卖价 = 3521-1 = 3520）
        broker.submit(market_command(Direction.SHORT, offset=Offset.CLOSE, qty="1"))
        broker.on_bar(make_bar(open_price="3521"))
        # 盈亏 = (3520-3501)*1*10 = 190；手续费 20
        assert broker.balance == Money(Decimal("100000") + Decimal("190") - Decimal("20"))

    def test_equity_includes_floating(self, broker: BacktestBroker) -> None:
        broker.submit(market_command(Direction.LONG, qty="1"))
        broker.on_bar(make_bar(open_price="3500"))
        # 持仓成本 3501，现价 3531 -> 浮盈 300
        equity = broker.equity({"rb2510": Price(Decimal("3531"))})
        assert equity == Money(Decimal("99990") + Decimal("300"))


class TestIdempotencyAndQuery:
    def test_duplicate_submit_returns_same_order(self, broker: BacktestBroker) -> None:
        command = market_command(Direction.LONG)
        first = broker.submit(command)
        second = broker.submit(command)
        assert first.order_id == second.order_id
        assert len(broker.orders) == 1

    def test_cancel(self, broker: BacktestBroker) -> None:
        order = broker.submit(limit_command("3400"))
        broker.cancel(order.order_id)
        assert order.status is OrderStatus.CANCELLED

    def test_query_missing_returns_none(self, broker: BacktestBroker) -> None:
        assert broker.query("NOPE") is None

    def test_on_bar_ignores_other_symbols(self, broker: BacktestBroker) -> None:
        broker.submit(market_command(Direction.LONG))
        other = make_bar()
        object.__setattr__(other, "symbol", "hc")  # frozen dataclass，测试专用
        filled = broker.on_bar(other)
        assert filled == []
