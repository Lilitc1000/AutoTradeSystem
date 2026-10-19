"""CTP 适配器测试（Fake openctp 模块，验证回调链与聚合链路，不需真连）。

覆盖：
- 行情：订阅（bytes list）、tick → BarAggregator → publish_bar（周期切换才发）
- 报单：认证→登录链、submit → ReqOrderInsert、OnRtnTrade → 成交 + fill 发布
- 账户：CtpAccount 代理 gateway 的资金/持仓快照
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from futures_trading.common.types import Price, Quantity
from futures_trading.trading.application.dtos import OrderCommand
from futures_trading.trading.domain.model.instrument import Instrument
from futures_trading.trading.domain.model.order import Offset, OrderStatus, PriceType
from futures_trading.trading.domain.model.signal import Direction
from futures_trading.trading.infrastructure.ctp import adapters as A

# ---- Fake CTP 模块 ----


class FakeField:
    """CTP 字段对象替身（setattr 任意属性）。"""


class FakeRspInfo:
    def __init__(self, error_id: int = 0, msg: str = "") -> None:
        self.ErrorID = error_id
        self.ErrorMsg = msg


class FakeLoginResp:
    TradingDay = "20260101"
    FrontID = 1
    SessionID = 1


class FakeBus:
    def __init__(self) -> None:
        self.bars: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []

    async def publish_bar(self, payload: dict[str, Any]) -> None:
        self.bars.append(payload)

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        if channel == "trade.fill":
            self.fills.append(payload)


class FakeMdApi:
    instances: list[FakeMdApi] = []

    def __init__(self, flow_path: str) -> None:
        self.flow_path = flow_path
        self.calls: list[tuple[str, Any]] = []
        self.spi: Any = None
        FakeMdApi.instances.append(self)

    @classmethod
    def CreateFtdcMdApi(cls, flow_path: str) -> FakeMdApi:
        return cls(flow_path)

    def RegisterSpi(self, spi: Any) -> None:
        self.spi = spi
        self.calls.append(("RegisterSpi", spi))

    def RegisterFront(self, addr: str) -> None:
        self.calls.append(("RegisterFront", addr))

    def Init(self) -> None:
        self.calls.append(("Init", None))
        if self.spi is not None:
            self.spi.OnFrontConnected()

    def ReqUserLogin(self, field: Any, request_id: int) -> None:
        self.calls.append(("ReqUserLogin", field))
        if self.spi is not None:
            self.spi.OnRspUserLogin(FakeLoginResp(), FakeRspInfo(0), request_id, True)

    def SubscribeMarketData(self, codes: list[bytes], count: int) -> None:
        self.calls.append(("SubscribeMarketData", (codes, count)))

    def Release(self) -> None:
        self.calls.append(("Release", None))


class FakeMdSpi:
    """spi 基类替身（适配器内部继承它）。"""


class FakeTdApi:
    instances: list[FakeTdApi] = []

    def __init__(self, flow_path: str) -> None:
        self.flow_path = flow_path
        self.calls: list[tuple[str, Any]] = []
        self.spi: Any = None
        FakeTdApi.instances.append(self)

    @classmethod
    def CreateFtdcTraderApi(cls, flow_path: str) -> FakeTdApi:
        return cls(flow_path)

    def RegisterSpi(self, spi: Any) -> None:
        self.spi = spi

    def RegisterFront(self, addr: str) -> None:
        self.calls.append(("RegisterFront", addr))

    def SubscribePrivateTopic(self, topic: int) -> None:
        return

    def SubscribePublicTopic(self, topic: int) -> None:
        return

    def Init(self) -> None:
        self.calls.append(("Init", None))
        if self.spi is not None:
            self.spi.OnFrontConnected()

    def ReqAuthenticate(self, field: Any, request_id: int) -> None:
        self.calls.append(("ReqAuthenticate", field))
        if self.spi is not None:
            self.spi.OnRspAuthenticate(FakeField(), FakeRspInfo(0), request_id, True)

    def ReqSettlementInfoConfirm(self, field: Any, request_id: int) -> None:
        self.calls.append(("ReqSettlementInfoConfirm", field))
        if self.spi is not None:
            self.spi.OnRspSettlementInfoConfirm(FakeField(), FakeRspInfo(0), request_id, True)

    def ReqUserLogin(self, field: Any, request_id: int) -> None:
        self.calls.append(("ReqUserLogin", field))
        if self.spi is not None:
            self.spi.OnRspUserLogin(FakeLoginResp(), FakeRspInfo(0), request_id, True)

    def ReqOrderInsert(self, field: Any, request_id: int) -> None:
        # 只记录发单；柜台回报由测试显式触发（保持 PENDING 语义可断言）
        self.calls.append(("ReqOrderInsert", field))

    def ReqOrderAction(self, field: Any, request_id: int) -> None:
        self.calls.append(("ReqOrderAction", field))

    def ReqQryTradingAccount(self, field: Any, request_id: int) -> None:
        self.calls.append(("ReqQryTradingAccount", field))
        account = FakeField()
        account.Available = "123456.78"
        if self.spi is not None:
            self.spi.OnRspQryTradingAccount(account, FakeRspInfo(0), request_id, True)

    def ReqQryInvestorPosition(self, field: Any, request_id: int) -> None:
        self.calls.append(("ReqQryInvestorPosition", field))
        if self.spi is not None:
            self.spi.OnRspQryInvestorPosition(None, FakeRspInfo(0), request_id, True)

    def Release(self) -> None:
        self.calls.append(("Release", None))


class FakeTdSpi:
    pass


class FakeMdModule:
    CThostFtdcMdApi = FakeMdApi
    CThostFtdcMdSpi = FakeMdSpi
    CThostFtdcReqUserLoginField = FakeField


class FakeTdModule:
    CThostFtdcTraderApi = FakeTdApi
    CThostFtdcTraderSpi = FakeTdSpi
    CThostFtdcReqAuthenticateField = FakeField
    CThostFtdcReqUserLoginField = FakeField
    CThostFtdcSettlementInfoConfirmField = FakeField
    CThostFtdcInputOrderField = FakeField
    CThostFtdcInputOrderActionField = FakeField
    CThostFtdcQryTradingAccountField = FakeField
    CThostFtdcQryInvestorPositionField = FakeField
    CThostFtdcQryOrderField = FakeField


@pytest.fixture(autouse=True)
def _fake_ctp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMdApi.instances.clear()
    FakeTdApi.instances.clear()
    monkeypatch.setattr(A, "_load_ctp", lambda: (FakeMdModule, FakeTdModule))


def _instrument(symbol: str = "rb") -> Instrument:
    return Instrument(
        symbol=symbol,
        contract=f"{symbol}2601",
        name="螺纹钢",
        exchange="SHFE",
        multiplier=Decimal("10"),
        tick_size=Decimal("1"),
        margin_rate=Decimal("0.1"),
    )


def _command() -> OrderCommand:
    return OrderCommand(
        instance_id="i1",
        contract="rb2601",
        symbol="rb",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        quantity=Quantity(Decimal("2")),
        price_type=PriceType.LIMIT,
        limit_price=Price(Decimal("3456.5")),
        signal_id="s1",
    )


def _depth(symbol: str, time_str: str, last: str) -> Any:
    field = FakeField()
    field.InstrumentID = symbol
    field.UpdateTime = time_str
    field.UpdateMillisec = 0
    field.LastPrice = last
    field.Volume = 10
    field.OpenInterest = 100
    field.BidPrice1 = last
    field.AskPrice1 = last
    return field


# ---- 行情 ----


class TestCtpMarketData:
    async def test_subscribe_before_start_only_records(self) -> None:
        md = A.CtpMarketData(A.CtpConfig(), FakeBus(), {"rb": _instrument()})
        md.subscribe(["rb"])
        assert md._subscribed == ["rb"]  # noqa: SLF001

    async def test_start_requests_subscribe_with_bytes(self) -> None:
        bus = FakeBus()
        md = A.CtpMarketData(A.CtpConfig(), bus, {"rb": _instrument()})
        md.subscribe(["rb"])
        await md.start()
        api = FakeMdApi.instances[0]
        subs = [c for c in api.calls if c[0] == "SubscribeMarketData"]
        assert subs, "登录后应补订"
        codes, count = subs[0][1]
        assert codes == [b"rb"]  # char*[] typemap 要 bytes
        assert count == 1
        await md.stop()

    async def test_tick_aggregates_and_publishes_bar_on_interval_cross(self) -> None:
        bus = FakeBus()
        md = A.CtpMarketData(A.CtpConfig(), bus, {"rb": _instrument()}, interval_minutes=1)
        md.subscribe(["rb"])
        await md.start()
        spi = md._spi  # noqa: SLF001
        # 同一分钟内：不闭合
        spi.OnRtnDepthMarketData(_depth("rb", "09:00:10", "3450"))
        spi.OnRtnDepthMarketData(_depth("rb", "09:00:30", "3455"))
        assert bus.bars == []
        # 跨分钟：闭合上一根
        spi.OnRtnDepthMarketData(_depth("rb", "09:01:05", "3460"))
        # 等消费协程发布
        import asyncio

        for _ in range(50):
            if bus.bars:
                break
            await asyncio.sleep(0.02)
        assert len(bus.bars) == 1
        assert bus.bars[0]["symbol"] == "rb"
        assert bus.bars[0]["close"] == "3455"  # 上一根收盘
        await md.stop()


# ---- 报单 ----


class TestCtpTradeGateway:
    async def test_start_runs_authenticate_then_login(self) -> None:
        gw = A.CtpTradeGateway(A.CtpConfig(), FakeBus())
        await gw.start()
        api = FakeTdApi.instances[0]
        names = [c[0] for c in api.calls]
        assert names.index("ReqAuthenticate") < names.index("ReqUserLogin")
        await gw.stop()

    async def test_submit_calls_req_order_insert_and_returns_pending(self) -> None:
        bus = FakeBus()
        gw = A.CtpTradeGateway(A.CtpConfig(), bus)
        await gw.start()
        order = gw.submit(_command())
        assert order.status is OrderStatus.PENDING
        api = FakeTdApi.instances[0]
        inserts = [c for c in api.calls if c[0] == "ReqOrderInsert"]
        assert len(inserts) == 1
        field = inserts[0][1]
        assert field.BrokerID == "9999"
        assert field.InstrumentID == "rb2601"
        assert gw.query(order.order_id) is order
        await gw.stop()

    async def test_rtn_trade_fills_order_and_publishes_fill(self) -> None:
        bus = FakeBus()
        gw = A.CtpTradeGateway(A.CtpConfig(), bus)
        await gw.start()
        order = gw.submit(_command())
        trade = FakeField()
        trade.OrderRef = order.order_id[-12:]
        trade.Volume = 2
        trade.Price = 3456.5
        trade.InstrumentID = "rb2601"
        # 柜台回报：先 SUBMITTED
        ack = FakeField()
        ack.OrderRef = order.order_id[-12:]
        ack.OrderStatus = "2"
        ack.OrderSubmitStatus = "0"
        ack.StatusMsg = ""
        gw._spi.OnRtnOrder(ack)  # noqa: SLF001
        assert order.status is OrderStatus.SUBMITTED
        gw._spi.OnRtnTrade(trade)  # noqa: SLF001
        assert order.status is OrderStatus.FILLED
        assert Decimal(order.avg_fill_price) == Decimal("3456.5")
        import asyncio

        for _ in range(50):
            if bus.fills:
                break
            await asyncio.sleep(0.02)
        assert bus.fills and bus.fills[0]["order_id"] == order.order_id
        await gw.stop()

    async def test_submit_without_session_raises(self) -> None:
        gw = A.CtpTradeGateway(A.CtpConfig(), FakeBus())
        with pytest.raises(RuntimeError):
            gw.submit(_command())


# ---- 账户 ----


class TestCtpAccount:
    async def test_balance_and_positions_via_gateway(self) -> None:
        gw = A.CtpTradeGateway(A.CtpConfig(), FakeBus())
        await gw.start()
        account = A.CtpAccount(gw)
        assert Decimal(account.balance()) == Decimal("123456.78")
        assert account.positions() == []
        await gw.stop()

    async def test_account_requires_ready_session(self) -> None:
        gw = A.CtpTradeGateway(A.CtpConfig(), FakeBus())
        account = A.CtpAccount(gw)
        with pytest.raises(RuntimeError):
            account.balance()
