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


# ---- CTP worker 子进程测试替身（崩溃隔离架构）----


class _FakeProcess:
    """用线程模拟 multiprocessing.Process（测试内不起真子进程）。"""

    def __init__(self, target: Any = None, args: tuple[Any, ...] = (), name: str = "", daemon: bool = True) -> None:
        self.target = target
        self.args = args
        self.pid = 12345
        self.exitcode: int | None = 0
        self._thread: Any = None

    def start(self) -> None:
        import threading as _threading

        self._thread = _threading.Thread(target=self.target, args=self.args, daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def terminate(self) -> None:
        self.exitcode = -15

    def join(self, timeout: float | None = None) -> None:
        return


class _FakeCtx:
    def Queue(self) -> Any:
        import queue as _queue

        return _queue.Queue()

    def Process(self, **kwargs: Any) -> _FakeProcess:
        return _FakeProcess(**kwargs)


def _install_fake_mp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(A.multiprocessing, "get_context", lambda _name: _FakeCtx())


def _fake_worker(  # noqa: PLR0913 - 对齐真 worker 签名
    q: Any,
    cmd_q: Any,
    config: Any,
    contracts: list[str],
    lifetime: float | None = None,
    mode: str = "md",
) -> None:
    """Fake CTP worker：塞预设回报 + 记录父进程下发的指令。"""
    _fake_worker.calls.append((list(contracts), lifetime, mode))  # type: ignore[attr-defined]
    for item in _fake_worker.queue_put:  # type: ignore[attr-defined]
        q.put(item)
    if _fake_worker.exit_immediately:  # type: ignore[attr-defined]
        return  # 模拟崩溃
    import queue as _q
    import time as _time

    deadline = _time.time() + 30
    while _time.time() < deadline:
        try:
            cmd = cmd_q.get_nowait()
        except _q.Empty:
            _time.sleep(0.02)
            continue
        _fake_worker.commands.append(cmd)  # type: ignore[attr-defined]
        # 按需触发预设回报（submit → 委托/成交回报）
        for item in _fake_worker.reply_on_cmd:  # type: ignore[attr-defined]
            q.put(item)


_fake_worker.calls = []  # type: ignore[attr-defined]
_fake_worker.queue_put = []  # type: ignore[attr-defined]
_fake_worker.commands = []  # type: ignore[attr-defined]
_fake_worker.reply_on_cmd = []  # type: ignore[attr-defined]
_fake_worker.exit_immediately = False  # type: ignore[attr-defined]


def _reset_fake_worker(queue_put: list[dict[str, Any]]) -> None:
    """重置 Fake worker 状态（每个用例前调用）。"""
    _fake_worker.calls.clear()  # type: ignore[attr-defined]
    _fake_worker.commands.clear()  # type: ignore[attr-defined]
    _fake_worker.reply_on_cmd = []  # type: ignore[attr-defined]
    _fake_worker.queue_put = queue_put  # type: ignore[attr-defined]
    _fake_worker.exit_immediately = False  # type: ignore[attr-defined]


def _depth_dict(symbol: str, time_str: str, last: str) -> dict[str, Any]:
    """worker 上报的 tick 字典（等价于 _field_to_dict(行情字段)）。"""
    return {
        "InstrumentID": symbol,
        "UpdateTime": time_str,
        "UpdateMillisec": 0,
        "LastPrice": last,
        "Volume": 10,
        "OpenInterest": 100,
        "BidPrice1": last,
        "AskPrice1": last,
    }


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

    async def test_start_spawns_worker_with_contracts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """父侧契约：启动 worker 子进程并传入订阅合约列表。"""
        _install_fake_mp(monkeypatch)
        _fake_worker.calls.clear()
        _fake_worker.queue_put = [{"type": A.READY, "trading_day": "20260101"}]
        monkeypatch.setattr(A, "run_ctp_worker", _fake_worker)
        md = A.CtpMarketData(A.CtpConfig(), FakeBus(), {"rb": _instrument()})
        md.subscribe(["rb"])
        await md.start()
        assert _fake_worker.calls, "应启动 CTP worker"
        contracts, _lifetime, _mode = _fake_worker.calls[0]
        assert contracts == ["rb"]
        assert _mode == "md"
        assert md._ready.is_set()  # noqa: SLF001  # READY 消息后置位
        await md.stop()

    async def test_worker_exit_triggers_restart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """崩溃隔离核心：worker 退出 → 父进程自动重启（计数递增）。"""
        _install_fake_mp(monkeypatch)
        _fake_worker.calls.clear()
        _fake_worker.queue_put = [{"type": A.READY, "trading_day": "20260101"}]
        _fake_worker.exit_immediately = True  # 模拟崩溃
        monkeypatch.setattr(A, "run_ctp_worker", _fake_worker)
        md = A.CtpMarketData(A.CtpConfig(), FakeBus(), {"rb": _instrument()})
        md.subscribe(["rb"])
        await md.start()
        # 监督协程每 2 秒巡检一次，等它发现 worker 退出并重启
        import asyncio

        for _ in range(40):
            if md._restart_count >= 1:  # noqa: SLF001
                break
            await asyncio.sleep(0.1)
        assert md._restart_count >= 1  # noqa: SLF001  # 监督协程已重启过
        _fake_worker.exit_immediately = False
        await md.stop()

    async def test_tick_from_worker_aggregates_and_publishes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """worker 上报 tick → 父进程聚合 → 跨周期发布 bar。"""
        import asyncio as _asyncio

        _install_fake_mp(monkeypatch)
        _fake_worker.calls.clear()
        _fake_worker.exit_immediately = False
        _fake_worker.queue_put = [
            {"type": A.READY, "trading_day": "20260101"},
            {"type": A.TICK, "data": _depth_dict("rb", "09:00:10", "3450")},
            {"type": A.TICK, "data": _depth_dict("rb", "09:00:30", "3455")},
            {"type": A.TICK, "data": _depth_dict("rb", "09:01:05", "3460")},
        ]
        monkeypatch.setattr(A, "run_ctp_worker", _fake_worker)
        bus = FakeBus()
        md = A.CtpMarketData(A.CtpConfig(), bus, {"rb": _instrument()}, interval_minutes=1)
        md.subscribe(["rb"])
        await md.start()
        for _ in range(80):
            if bus.bars:
                break
            await _asyncio.sleep(0.02)
        assert len(bus.bars) == 1
        assert bus.bars[0]["symbol"] == "rb"
        assert bus.bars[0]["close"] == "3455"  # 上一根收盘
        await md.stop()


# ---- 报单 ----


class TestCtpTradeGateway:
    async def test_start_spawns_td_worker_and_waits_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """交易侧：起 td worker 子进程（认证链在 worker 内），等 READY 置位。"""
        _install_fake_mp(monkeypatch)
        _reset_fake_worker([{"type": A.READY, "front_id": 1, "session_id": 2}])
        monkeypatch.setattr(A, "run_ctp_worker", _fake_worker)
        gw = A.CtpTradeGateway(A.CtpConfig(), FakeBus())
        await gw.start()
        assert _fake_worker.calls, "应启动交易 worker"
        _contracts, _lifetime, mode = _fake_worker.calls[0]
        assert mode == "td"
        assert gw._ready.is_set()  # noqa: SLF001
        assert gw._front_id == 1 and gw._session_id == 2  # noqa: SLF001  # 撤单定位
        await gw.stop()

    async def test_submit_sends_cmd_to_worker_and_returns_pending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """发单：指令经队列下发 worker（CTP 调用不在父进程）。"""
        _install_fake_mp(monkeypatch)
        _reset_fake_worker([{"type": A.READY, "front_id": 1, "session_id": 2}])
        monkeypatch.setattr(A, "run_ctp_worker", _fake_worker)
        bus = FakeBus()
        gw = A.CtpTradeGateway(A.CtpConfig(), bus)
        await gw.start()
        order = gw.submit(_command())
        assert order.status is OrderStatus.PENDING
        # worker 侧轮询收指令，等它被 Fake worker 取走
        import asyncio

        for _ in range(50):
            if _fake_worker.commands:
                break
            await asyncio.sleep(0.02)
        inserts = [c for c in _fake_worker.commands if c.get("action") == "submit"]
        assert len(inserts) == 1
        values = inserts[0]["values"]
        assert values["BrokerID"] == "9999"
        assert values["InstrumentID"] == "rb2601"
        assert values["OrderPriceType"] == "2"  # 限价（编码反直觉）
        assert gw.query(order.order_id) is order
        await gw.stop()

    async def test_rtn_trade_fills_order_and_publishes_fill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_mp(monkeypatch)
        _reset_fake_worker([{"type": A.READY, "front_id": 1, "session_id": 2}])
        monkeypatch.setattr(A, "run_ctp_worker", _fake_worker)
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
        gw._on_order(  # noqa: SLF001  # 父进程侧回报处理（worker 上报）
            {"OrderRef": ack.OrderRef, "OrderStatus": "2", "OrderSubmitStatus": "0", "StatusMsg": ""}
        )
        assert order.status is OrderStatus.SUBMITTED
        await gw._on_trade(  # noqa: SLF001
            {"OrderRef": trade.OrderRef, "InstrumentID": "rb2601", "Volume": "2", "Price": "3456.5"}
        )
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
    async def test_balance_and_positions_via_gateway(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_mp(monkeypatch)
        # worker 收到 query_account 指令后回复资金快照
        _reset_fake_worker([{"type": A.READY, "front_id": 1, "session_id": 2}])
        _fake_worker.reply_on_cmd = [{"type": A.ACCOUNT, "data": {"Available": "123456.78"}}]
        monkeypatch.setattr(A, "run_ctp_worker", _fake_worker)
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
