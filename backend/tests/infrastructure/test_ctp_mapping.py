"""CTP 映射测试（纯函数，不依赖 openctp 库）。"""

from __future__ import annotations

from decimal import Decimal

from futures_trading.common.types import Price, Quantity
from futures_trading.trading.application.dtos import OrderCommand, Tick
from futures_trading.trading.domain.model.order import Offset, OrderStatus, PriceType
from futures_trading.trading.domain.model.signal import Direction
from futures_trading.trading.infrastructure.ctp import mapping


class TestEnumMapping:
    def test_direction(self) -> None:
        assert mapping.direction_to_ctp(Direction.LONG) == "0"
        assert mapping.direction_to_ctp(Direction.SHORT) == "1"

    def test_offset(self) -> None:
        assert mapping.offset_to_ctp(Offset.OPEN) == "0"
        assert mapping.offset_to_ctp(Offset.CLOSE) == "1"

    def test_price_type(self) -> None:
        # CTP 编码反直觉：'1'=任意价(市价) '2'=限价（夜盘联调 79 号错误实锤）
        assert mapping.price_type_to_ctp(PriceType.LIMIT) == "2"
        assert mapping.price_type_to_ctp(PriceType.MARKET) == "1"

    def test_ctp_status_to_domain(self) -> None:
        assert mapping.ctp_to_order_status("0") is OrderStatus.FILLED
        assert mapping.ctp_to_order_status("1") is OrderStatus.PARTIAL
        assert mapping.ctp_to_order_status("2") is OrderStatus.SUBMITTED
        assert mapping.ctp_to_order_status("3") is OrderStatus.CANCELLED
        assert mapping.ctp_to_order_status("5") is OrderStatus.PENDING
        # 报单被拒绝优先于状态位
        assert mapping.ctp_to_order_status("2", "4") is OrderStatus.REJECTED


class TestCommandMapping:
    def test_market_open_long(self) -> None:
        command = OrderCommand(
            instance_id="i1",
            contract="rb2510",
            symbol="rb",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            quantity=Quantity(Decimal("2")),
            price_type=PriceType.MARKET,
        )
        ctp = mapping.command_to_ctp_input(command)
        assert ctp["Direction"] == "0"
        assert ctp["CombOffsetFlag"] == "0"
        assert ctp["OrderPriceType"] == "1"  # CTP: '1'=任意价(市价)
        assert ctp["VolumeTotalOriginal"] == 2
        assert ctp["LimitPrice"] == 0.0

    def test_limit_close_short(self) -> None:
        command = OrderCommand(
            instance_id="i1",
            contract="rb2510",
            symbol="rb",
            direction=Direction.SHORT,
            offset=Offset.CLOSE,
            quantity=Quantity(Decimal("3")),
            price_type=PriceType.LIMIT,
            limit_price=Price(Decimal("3456.5")),
        )
        ctp = mapping.command_to_ctp_input(command)
        assert ctp["Direction"] == "1"
        assert ctp["CombOffsetFlag"] == "1"
        assert ctp["OrderPriceType"] == "2"  # CTP: '2'=限价（编码反直觉）
        assert ctp["LimitPrice"] == 3456.5


class TestReportMapping:
    def test_trade_field_to_fill(self) -> None:
        fill = mapping.ctp_trade_to_fill(
            {
                "InstrumentID": "rb2510",
                "Direction": "0",
                "OffsetFlag": "0",
                "Volume": "2",
                "Price": "3500.5",
                "Commission": "12.3",
                "TradeID": "T1",
                "OrderRef": "R1",
                "TradeTime": "09:35:00",
            }
        )
        assert fill["contract"] == "rb2510"
        assert fill["direction"] == "LONG"
        assert fill["quantity"] == Decimal("2")
        assert fill["price"] == Decimal("3500.5")
        assert fill["commission"] == Decimal("12.3")

    def test_depth_to_tick(self) -> None:
        tick = mapping.ctp_depth_to_tick(
            {
                "LastPrice": "3501.0",
                "Volume": "12345",
                "OpenInterest": "999",
                "BidPrice1": "3500.0",
                "AskPrice1": "3502.0",
                "UpdateTime": "09:35:00",
                "UpdateMillisec": 500,
            },
            symbol="rb",
        )
        assert isinstance(tick, Tick)
        assert tick.symbol == "rb"
        assert Decimal(tick.last_price) == Decimal("3501.0")
        assert Decimal(tick.volume) == Decimal("12345")
        assert tick.ts.tzinfo is None  # CTP 时间是本地无时区


class TestOffsetMapping:
    """SHFE 平今/平昨映射（日盘 10-20 补）。"""

    def test_close_today_and_yesterday_flags(self) -> None:
        assert mapping.offset_to_ctp(Offset.OPEN) == "0"
        assert mapping.offset_to_ctp(Offset.CLOSE_TODAY) == "3"  # SHFE 平今
        assert mapping.offset_to_ctp(Offset.CLOSE_YESTERDAY) == "1"  # SHFE 平昨
        assert mapping.offset_to_ctp(Offset.CLOSE) == "1"  # 通用平仓
