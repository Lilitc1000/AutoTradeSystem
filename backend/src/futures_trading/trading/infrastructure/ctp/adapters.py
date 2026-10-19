"""CTP 适配器：三端口真连实现（M2-d）。

设计要点：
- **懒加载**：openctp_ctp 是 C++ 扩展（仅 Linux wheel），模块级不 import；
  Windows 开发机不装它也不影响 mypy/ruff/pytest（适配器 start() 时才加载）。
- **线程模型**：CTP 的 spi 回调在 C++ 线程（同步），而 MessageBus.publish 是
  异步——回调里只做「换算 + 入队」，由 asyncio 消费协程真正发布（线程安全）。
- **已知坑（探针实踩，见 scripts/ctp_probe/）**：
  - SubscribeMarketData 要 list of **bytes**（char*[] typemap）
  - md api 无 Authenticate；td api 必须先 ReqAuthenticate 才能登录
  - 字段对象（CThostFtdcXxxField）而非 dict 作为入参
  - 退出时可能 segfault（openctp atexit 析构，已知技术债）
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from ....common.settings import settings
from ....common.types import Money, Quantity
from ...application.dtos import Bar, OrderCommand
from ...application.ports import AccountPort, MarketDataPort, OrderPort
from ...domain.model.instrument import Instrument
from ...domain.model.order import Order, OrderStatus, PriceType
from ..bar_aggregator import BarAggregator
from . import mapping

logger = logging.getLogger(__name__)


def _load_ctp() -> tuple[Any, Any]:
    """懒加载 CTP API（返回 (md_api, trade_api) 模块）。"""
    try:
        from openctp_ctp import thostmduserapi as md_api
        from openctp_ctp import thosttraderapi as td_api
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise RuntimeError(
            "openctp_ctp 未安装或不可加载（Linux C++ 扩展）。"
            "M2-b 联调前执行: pip install openctp-ctp 并确认容器 locale 正常"
        ) from exc
    return md_api, td_api


def _to_field(values: dict[str, Any], field_cls: type[Any]) -> Any:
    """dict → CTP 字段对象（SWIG 入参要求结构体，非 dict）。"""
    field = field_cls()
    for key, value in values.items():
        if value is None:
            continue
        setattr(field, key, value)
    return field


class CtpConfig:
    """CTP 连接配置（默认从 settings 读，SimNow 凭证走 .env）。"""

    def __init__(
        self,
        broker_id: str = "9999",
        user_id: str = "",
        password: str = "",
        app_id: str = "simnow_client_test",
        auth_code: str = "0000000000000000",
        md_front: str = "tcp://182.254.243.31:40011",
        trade_front: str = "tcp://182.254.243.31:40001",
        flow_dir: str = "/tmp/futures_ctp",
    ) -> None:
        """flow_dir：CTP 流文件目录（必须本地可写；容器 /workspace 是 9p 挂载，
        CTP 在其上创建 CFlow 文件会失败，故默认放 /tmp）。"""
        self.broker_id = broker_id
        self.user_id = user_id
        self.password = password
        self.app_id = app_id
        self.auth_code = auth_code
        self.md_front = md_front
        self.trade_front = trade_front
        self.flow_dir = flow_dir

    def flow_path(self, name: str) -> str:
        """确保 flow 子目录存在并返回（CTP CreateFtdcXxxApi 入参）。"""
        import os  # noqa: PLC0415 - 仅此处需要

        path = os.path.join(self.flow_dir, name)
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def from_settings(cls) -> CtpConfig:
        return cls(
            broker_id=settings.ctp_broker_id,
            user_id=settings.simnow_user_id,
            password=settings.simnow_password,
            app_id=settings.simnow_app_id,
            auth_code=settings.simnow_auth_code,
            md_front=settings.ctp_md_front,
            trade_front=settings.ctp_trade_front,
        )


class CtpMarketData(MarketDataPort):
    """CTP 行情适配器：spi 回调 → BarAggregator → MessageBus.publish_bar。

    用法（异步）：
        md = CtpMarketData(config, bus, instruments)
        md.subscribe(["rb2601"])
        await md.start()      # 连接、登录、订阅、起消费协程
        ...
        await md.stop()       # flush 最后一根 + Release
    """

    def __init__(
        self,
        config: CtpConfig,
        bus: Any,
        instruments: dict[str, Instrument] | None = None,
        interval_minutes: int = 1,
    ) -> None:
        self.config = config
        self.bus = bus
        self.instruments = instruments or {}
        self.interval_minutes = interval_minutes
        self._aggregators: dict[str, BarAggregator] = {}
        self._contract_to_symbol: dict[str, str] = {}
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tick_count = 0
        self._api: Any = None
        self._subscribed: list[str] = []
        self._ready = threading.Event()
        self._consumer: asyncio.Task[None] | None = None

    # ---- MarketDataPort ----

    def subscribe(self, symbols: Sequence[str]) -> None:
        missing = [s for s in symbols if s not in self.instruments]
        if missing:
            raise ValueError(f"品种未注册: {missing}")
        self._subscribed = list(symbols)
        # 合约 → 品种映射（CTP 订阅用合约代码，聚合/总线用品种）
        self._contract_to_symbol = {c: inst.symbol for c, inst in self.instruments.items()}
        for s in symbols:
            self._aggregators.setdefault(s, BarAggregator(s, self.interval_minutes))
        # 已连接则立即订阅（未连接时由 start() 在登录后补订）
        if self._api is not None and self._ready.is_set():
            self._request_subscribe()

    def latest_bar(self, symbol: str, interval_minutes: int) -> Bar | None:
        agg = self._aggregators.get(symbol)
        if agg is None or agg.interval_minutes != interval_minutes:
            return None
        return agg.flush()

    # ---- 生命周期 ----

    async def start(self) -> None:
        md_api, _ = _load_ctp()
        self._api = md_api.CThostFtdcMdApi.CreateFtdcMdApi(self.config.flow_path("flow_md"))
        self._spi = self._make_spi(md_api.CThostFtdcMdSpi)
        self._api.RegisterSpi(self._spi)
        self._api.RegisterFront(self.config.md_front)
        self._api.Init()
        # 等登录（CTP 回调在独立线程，超时不阻塞主流程）
        await asyncio.to_thread(self._ready.wait, 20)
        self._loop = asyncio.get_running_loop()
        self._consumer = asyncio.create_task(self._consume())
        self._request_subscribe()

    async def stop(self) -> None:
        if self._consumer is not None:
            # flush 未闭合的 bar 一并发布
            for agg in self._aggregators.values():
                bar = agg.flush()
                if bar is not None:
                    await self.bus.publish_bar(_bar_payload(bar))
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._consumer, timeout=5)
            except (TimeoutError, asyncio.CancelledError):  # noqa: UP041
                self._consumer.cancel()
            self._consumer = None
        if self._api is not None:
            self._api.Release()
            self._api = None

    # ---- 内部 ----

    def _request_subscribe(self) -> None:
        if not self._subscribed or self._api is None:
            return
        codes = [s.encode("utf-8") for s in self._subscribed]
        self._api.SubscribeMarketData(codes, len(codes))

    def _make_spi(self, spi_base: type[Any]) -> Any:
        owner = self

        class _MdSpi(spi_base):  # type: ignore[misc, valid-type]
            def OnFrontConnected(self) -> None:
                if owner._api is None:
                    return
                field = _to_field(
                    {
                        "BrokerID": owner.config.broker_id,
                        "UserID": owner.config.user_id,
                        "Password": owner.config.password,
                    },
                    _load_ctp()[0].CThostFtdcReqUserLoginField,
                )
                owner._api.ReqUserLogin(field, 1)

            def OnRspUserLogin(self, pRspUserLogin: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
                if pRspInfo is not None and pRspInfo.ErrorID != 0:
                    return
                owner._ready.set()

            def OnRtnDepthMarketData(self, pDepthMarketData: Any) -> None:
                payload = _field_to_dict(pDepthMarketData)
                contract = str(payload.get("InstrumentID", ""))
                if not contract:
                    return
                # 聚合按合约（每合约独立 K 线）；发总线按品种（下游统一）
                symbol = owner._contract_to_symbol.get(contract, contract)
                tick = mapping.ctp_depth_to_tick(payload, symbol=contract)
                owner._tick_count += 1
                if owner._tick_count % 20 == 1:
                    logger.info(
                        "CTP tick 流: %s 第 %d 笔 last=%s",
                        contract,
                        owner._tick_count,
                        payload.get("LastPrice"),
                    )
                agg = owner._aggregators.get(contract)
                if agg is None:
                    agg = BarAggregator(contract, owner.interval_minutes)
                    owner._aggregators[contract] = agg
                bar = agg.on_tick(tick)
                if bar is not None and owner._loop is not None:
                    owner._loop.call_soon_threadsafe(
                        owner._queue.put_nowait, _bar_payload(bar, symbol=symbol)
                    )

            def OnRspError(self, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
                return

        return _MdSpi()

    async def _consume(self) -> None:
        while True:
            payload = await self._queue.get()
            if payload is None:
                return
            await self.bus.publish_bar(payload)


class CtpTradeGateway(OrderPort):
    """CTP 报单适配器：认证 → 登录 → 报单 → 回报 → 域 Order → 总线。

    看穿式监管流程（官方 td_demo 同款）：
        OnFrontConnected → ReqAuthenticate → OnRspAuthenticate → ReqUserLogin
    """

    def __init__(
        self,
        config: CtpConfig,
        bus: Any,
        instruments: dict[str, Instrument] | None = None,
    ) -> None:
        self.config = config
        self.bus = bus
        self.instruments = instruments or {}
        self._orders: dict[str, Order] = {}
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._api: Any = None
        self._ready = threading.Event()
        self._request_id = 0
        self._consumer: asyncio.Task[None] | None = None
        # 账户快照（由 CtpAccount 代理读取）
        self._spi: Any = None
        self._balance: Decimal = Decimal("0")
        self._positions: list[Any] = []
        self._account_waiter = threading.Event()

    # ---- OrderPort ----

    def submit(self, command: OrderCommand) -> Order:
        if self._api is None or not self._ready.is_set():
            raise RuntimeError("交易会话未就绪，请先 await start()")
        order = Order(
            order_id=f"{command.instance_id}-{command.idempotency_key}",
            idempotency_key=command.idempotency_key,
            instance_id=command.instance_id,
            contract=command.contract,
            symbol=command.symbol,
            direction=command.direction,
            offset=command.offset,
            quantity=command.quantity,
            price_type=command.price_type,
            limit_price=command.limit_price,
            signal_id=command.signal_id,
        )
        self._orders[order.order_id] = order
        _md, trade = _load_ctp()
        values = mapping.command_to_ctp_input(command)
        # 柜台必填字段（mapping 只做纯映射，连接相关字段由适配器补）
        values["InstrumentID"] = command.contract
        values.setdefault("BrokerID", self.config.broker_id)
        values.setdefault("InvestorID", self.config.user_id)
        values.setdefault("UserID", self.config.user_id)
        values.setdefault("OrderRef", order.order_id[-12:])
        if command.price_type is PriceType.MARKET:
            values["LimitPrice"] = Decimal("0")
        field = _to_field(values, trade.CThostFtdcInputOrderField)
        self._request_id += 1
        self._api.ReqOrderInsert(field, self._request_id)
        return order

    def cancel(self, order_id: str) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"订单不存在: {order_id}")
        if self._api is None:
            raise RuntimeError("交易会话未就绪")
        _md, trade = _load_ctp()
        values = {
            "BrokerID": self.config.broker_id,
            "InvestorID": self.config.user_id,
            "OrderRef": order_id[-12:],
            "InstrumentID": order.contract,
            "ActionFlag": "0",
        }
        self._request_id += 1
        self._api.ReqOrderAction(_to_field(values, trade.CThostFtdcInputOrderActionField), self._request_id)
        return order

    def query(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    # ---- 生命周期 ----

    async def start(self) -> None:
        _md, trade = _load_ctp()
        self._api = trade.CThostFtdcTraderApi.CreateFtdcTraderApi(self.config.flow_path("flow_td"))
        self._spi = self._make_spi(trade.CThostFtdcTraderSpi)
        self._api.RegisterSpi(self._spi)
        self._api.RegisterFront(self.config.trade_front)
        self._api.SubscribePrivateTopic(2)  # 1=只推登录后的流
        self._api.SubscribePublicTopic(2)
        self._api.Init()
        await asyncio.to_thread(self._ready.wait, 20)
        self._loop = asyncio.get_running_loop()
        self._consumer = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._consumer is not None:
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._consumer, timeout=5)
            except (TimeoutError, asyncio.CancelledError):  # noqa: UP041
                self._consumer.cancel()
            self._consumer = None
        if self._api is not None:
            self._api.Release()
            self._api = None

    # ---- 账户查询（AccountPort 由 CtpAccount 代理到此）----

    def balance_snapshot(self) -> Money:
        """查询可用资金（同步等回调，10 秒超时）。"""
        if self._api is None or not self._ready.is_set():
            raise RuntimeError("交易会话未就绪")
        _md, trade = _load_ctp()
        self._account_waiter = threading.Event()
        self._api.ReqQryTradingAccount(
            _to_field(
                {"BrokerID": self.config.broker_id, "InvestorID": self.config.user_id},
                trade.CThostFtdcQryTradingAccountField,
            ),
            self._next_request_id(),
        )
        self._account_waiter.wait(timeout=10)
        return Money(self._balance)

    def position_snapshot(self) -> list[Any]:
        """查询持仓（同步等回调）。"""
        if self._api is None or not self._ready.is_set():
            raise RuntimeError("交易会话未就绪")
        _md, trade = _load_ctp()
        self._account_waiter = threading.Event()
        self._positions = []
        self._api.ReqQryInvestorPosition(
            _to_field(
                {"BrokerID": self.config.broker_id, "InvestorID": self.config.user_id},
                trade.CThostFtdcQryInvestorPositionField,
            ),
            self._next_request_id(),
        )
        self._account_waiter.wait(timeout=10)
        return list(self._positions)

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def sync_state(self) -> None:
        """重启对账：查询柜台订单，重建本地订单状态（M1 设计原则）。"""
        if self._api is None or not self._ready.is_set():
            raise RuntimeError("交易会话未就绪")
        _md, trade = _load_ctp()
        values = {
            "BrokerID": self.config.broker_id,
            "InvestorID": self.config.user_id,
        }
        self._request_id += 1
        self._api.ReqQryOrder(_to_field(values, trade.CThostFtdcQryOrderField), self._request_id)

    # ---- 内部 ----

    def _make_spi(self, spi_base: type[Any]) -> Any:
        owner = self

        class _TdSpi(spi_base):  # type: ignore[misc, valid-type]
            def OnFrontConnected(self) -> None:
                _md, trade = _load_ctp()
                field = _to_field(
                    {
                        "BrokerID": owner.config.broker_id,
                        "UserID": owner.config.user_id,
                        "AppID": owner.config.app_id,
                        "AuthCode": owner.config.auth_code,
                    },
                    trade.CThostFtdcReqAuthenticateField,
                )
                owner._api.ReqAuthenticate(field, 1)

            def OnRspAuthenticate(
                self, pRspAuthenticateField: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool
            ) -> None:
                if pRspInfo is not None and pRspInfo.ErrorID != 0:
                    return
                _md, trade = _load_ctp()
                field = _to_field(
                    {
                        "BrokerID": owner.config.broker_id,
                        "UserID": owner.config.user_id,
                        "Password": owner.config.password,
                    },
                    trade.CThostFtdcReqUserLoginField,
                )
                owner._api.ReqUserLogin(field, 2)

            def OnRspUserLogin(self, pRspUserLogin: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
                if pRspInfo is not None and pRspInfo.ErrorID != 0:
                    return
                owner._ready.set()

            def OnRtnOrder(self, pOrder: Any) -> None:
                payload = _field_to_dict(pOrder)
                order_ref_tail = str(payload.get("OrderRef", ""))
                order = owner._match_order(order_ref_tail)
                if order is None:
                    return
                status = mapping.ctp_to_order_status(
                    str(payload.get("OrderStatus", "")), str(payload.get("OrderSubmitStatus", "0"))
                )
                if status is OrderStatus.SUBMITTED and order.status is OrderStatus.PENDING:
                    order.submit()
                elif status is OrderStatus.CANCELLED and not order.is_final:
                    order.cancel()
                elif status is OrderStatus.REJECTED and not order.is_final:
                    order.reject(str(payload.get("StatusMsg", "")))

            def OnRtnTrade(self, pTrade: Any) -> None:
                fill = mapping.ctp_trade_to_fill(_field_to_dict(pTrade))
                order = owner._match_order(str(fill.get("order_ref", "")))
                if order is None:
                    return
                qty = Decimal(str(fill.get("quantity", "0")))
                price = Decimal(str(fill.get("price", "0")))
                if qty > 0:
                    order.partial_fill(Quantity(qty), price)
                if owner._loop is not None:
                    owner._loop.call_soon_threadsafe(
                        owner._queue.put_nowait,
                        {
                            "order_id": order.order_id,
                            "status": str(order.status),
                            "filled_quantity": str(order.filled_quantity),
                            "avg_fill_price": str(order.avg_fill_price),
                            **fill,
                        },
                    )

            def OnRspQryOrder(self, pOrder: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
                """对账：柜台订单回填本地（仅重建仍存在的订单）。"""
                if pOrder is None:
                    return
                payload = _field_to_dict(pOrder)
                order = owner._match_order(str(payload.get("OrderRef", "")))
                if order is None:
                    return
                status = mapping.ctp_to_order_status(
                    str(payload.get("OrderStatus", "")), str(payload.get("OrderSubmitStatus", "0"))
                )
                # 状态迁移必须走聚合根的合法路径（非法迁移说明本地状态已过期，跳过）
                owner._apply_status(order, status)

            def OnRspQryTradingAccount(
                self, pTradingAccount: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool
            ) -> None:
                if pTradingAccount is None:
                    return
                payload = _field_to_dict(pTradingAccount)
                available = payload.get("Available")
                if available not in (None, ""):
                    owner._balance = Decimal(str(available))
                if bIsLast:
                    owner._account_waiter.set()

            def OnRspQryInvestorPosition(
                self, pInvestorPosition: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool
            ) -> None:
                if pInvestorPosition is None:
                    return
                payload = _field_to_dict(pInvestorPosition)
                if str(payload.get("Position", "0")) != "0":
                    owner._positions.append(payload)
                if bIsLast:
                    owner._account_waiter.set()

            def OnRspError(self, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
                return

        return _TdSpi()

    def _match_order(self, order_ref_tail: str) -> Order | None:
        for order_id, order in self._orders.items():
            if order_ref_tail and order_id.endswith(order_ref_tail):
                return order
        return None

    def _apply_status(self, order: Order, status: OrderStatus) -> None:
        """按状态机合法路径推进；非法迁移（本地已过期）忽略。"""
        try:
            if status is OrderStatus.SUBMITTED and order.status is OrderStatus.PENDING:
                order.submit()
            elif status is OrderStatus.CANCELLED and not order.is_final:
                order.cancel()
            elif status is OrderStatus.REJECTED and not order.is_final:
                order.reject("CTP 回报拒绝")
        except Exception:  # noqa: BLE001 - 非法迁移不致命（状态机已记录原因）
            return

    async def _consume(self) -> None:
        while True:
            payload = await self._queue.get()
            if payload is None:
                return
            await self.bus.publish("trade.fill", payload)


class CtpAccount(AccountPort):
    """CTP 账户视图：复用 CtpTradeGateway 的会话（一个 api 一个会话）。

    用法：
        gateway = CtpTradeGateway(config, bus)
        await gateway.start()
        account = CtpAccount(gateway)
        account.balance()      # 同步查询（内部等回调）
    """

    def __init__(self, gateway: CtpTradeGateway) -> None:
        self._gateway = gateway

    def balance(self) -> Money:
        return self._gateway.balance_snapshot()

    def positions(self) -> list[Any]:
        return self._gateway.position_snapshot()


def _field_to_dict(field: Any) -> dict[str, Any]:
    """CTP 字段对象 → dict（取所有非私有属性）。"""
    if field is None:
        return {}
    return {k: getattr(field, k) for k in dir(field) if not k.startswith("_") and not callable(getattr(field, k, None))}


def _bar_payload(bar: Bar, symbol: str | None = None) -> dict[str, Any]:
    """Bar → 总线 payload。symbol 覆盖为品种级（bar.symbol 是合约代码）。"""
    return {
        "symbol": symbol or bar.symbol,
        "contract": bar.symbol,
        "interval_minutes": bar.interval_minutes,
        "ts": bar.ts.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "open_interest": str(bar.open_interest),
    }


__all__ = ["CtpConfig", "CtpMarketData", "CtpTradeGateway", "CtpAccount"]
