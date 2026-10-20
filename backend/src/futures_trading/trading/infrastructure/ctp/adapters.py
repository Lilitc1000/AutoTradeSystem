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
import multiprocessing
import threading
import time
from collections.abc import Sequence
from decimal import Decimal
from queue import Empty
from typing import Any

from ....common.settings import settings
from ....common.types import Money, Quantity
from ...application.dtos import Bar, OrderCommand
from ...application.ports import AccountPort, MarketDataPort, OrderPort
from ...domain.model.instrument import Instrument
from ...domain.model.order import Order, OrderStatus, PriceType
from ..bar_aggregator import BarAggregator
from . import mapping
from .worker import ACCOUNT, DISCONNECTED, ORDER, POSITION, READY, TICK, TRADE, run_ctp_worker

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

    @classmethod
    async def from_active_account(cls) -> CtpConfig:
        """激活账号（DB，M3-e）优先；无激活账号回落 .env（迁移期兼容）。

        凭证在库里加密——解密只在构造 CtpConfig 时发生，不落日志。
        """
        from ...application.account_service import AccountService  # noqa: PLC0415 - 防循环导入

        values = await AccountService().active_config_dict()
        if values is None:
            logger.info("无激活交易账号，回落 .env 配置")
            return cls.from_settings()
        return cls(**values)


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
        self._spi: Any = None
        self._ctp_thread: threading.Thread | None = None
        self._subscribed: list[str] = []
        self._ready = threading.Event()
        self._consumer: asyncio.Task[None] | None = None
        # ---- CTP worker 子进程（崩溃隔离，见 worker.py）----
        self._mp_queue: Any = None  # multiprocessing.Queue（父↔子）
        self._worker: Any = None  # multiprocessing.Process
        self._supervisor: asyncio.Task[None] | None = None
        self._worker_lifetime: float | None = None
        self._restart_count = 0
        self._stopping = False

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

    @classmethod
    def create(
        cls,
        config: CtpConfig,
        bus: Any,
        instruments: dict[str, Instrument] | None = None,
        interval_minutes: int = 1,
        worker_lifetime: float | None = None,
    ) -> CtpMarketData:
        """同步工厂：启动 CTP worker **子进程** 并等登录就绪。

        为什么改成子进程（M2 工程化）：openctp 的 SWIG 崩溃发生在 C++ 扩展侧，
        同进程会带走整个 worker（聚合状态/Redis 连接/心跳全丢，夜盘实测崩率
        ~50%）。隔离后崩溃只杀子进程，父进程重启即可，业务状态无感（vnpy 架构）。

        worker_lifetime：子进程存活秒数（None=常驻）；到期主动退出由父进程重启，
        兼作禁 GC 进程的内存重置机制。

        注意：本方法只构造对象——worker 在 run()/start() 里启动（那时
        subscribe() 已完成，合约列表才不为空；spawn 子进程是全新解释器，
        不存在"asyncio 帧创建 api"的 SWIG 约束）。
        """
        self = cls(config, bus, instruments, interval_minutes)
        self._worker_lifetime = worker_lifetime
        return self

    async def start(self) -> None:
        """一步启动（测试/Fake 场景）：起 worker + 等就绪 + 起消费协程，不常驻。"""
        if self._worker is None:
            self._start_worker()
            await asyncio.to_thread(self._drain_until_ready, 20)
        self._loop = asyncio.get_running_loop()
        self._consumer = asyncio.create_task(self._consume())
        # 监督协程（崩溃重启）在 start/run 两条路径都要起——否则 start 场景
        # worker 崩了无人重启
        self._supervisor = asyncio.create_task(self._supervise())

    def _start_worker(self) -> None:
        """启动（或重启）CTP worker 子进程。"""
        ctx = multiprocessing.get_context("spawn")
        if self._mp_queue is None:
            self._mp_queue = ctx.Queue()
        self._worker = ctx.Process(
            target=run_ctp_worker,
            args=(self._mp_queue, None, self.config, list(self._subscribed), self._worker_lifetime, "md"),
            name="ctp-md-worker",
            daemon=True,
        )
        self._worker.start()
        logger.info("CTP worker 子进程启动 pid=%s contracts=%s", self._worker.pid, self._subscribed)

    def _drain_until_ready(self, timeout: float) -> None:
        """同步消费队列直到 worker 登录就绪（create/start 阶段用）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._ready.is_set():
            try:
                item = self._mp_queue.get(timeout=0.2)
            except Empty:
                continue
            if isinstance(item, dict) and item.get("type") == READY:
                self._ready.set()
                return

    async def run(self, max_runtime_seconds: int = 0) -> None:
        """asyncio 侧：消费 worker 队列 + 监督子进程 + 常驻。

        max_runtime_seconds>0：到时父进程主动退出（守护重启，重置内存）。
        """
        self._loop = asyncio.get_running_loop()
        # worker 在 subscribe 之后启动（合约列表此时已就绪）
        if self._worker is None:
            self._start_worker()
            await asyncio.to_thread(self._drain_until_ready, 20)
        self._consumer = asyncio.create_task(self._consume())
        self._supervisor = asyncio.create_task(self._supervise())
        started = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(1)
            if max_runtime_seconds > 0 and asyncio.get_running_loop().time() - started >= max_runtime_seconds:
                logger.info("到达 max_runtime=%ds，主动退出（守护将重启）", max_runtime_seconds)
                return

    async def _supervise(self) -> None:
        """子进程监督：worker 崩溃/退出则重启（崩溃隔离的核心逻辑）。"""
        while True:
            await asyncio.sleep(2)
            if self._stopping or self._worker is None:
                continue
            if not self._worker.is_alive():
                self._restart_count += 1
                logger.warning(
                    "CTP worker 退出（exitcode=%s），第 %d 次重启", self._worker.exitcode, self._restart_count
                )
                self._ready.clear()
                self._start_worker()

    async def stop(self) -> None:
        self._stopping = True
        if self._consumer is not None:
            # flush 未闭合的 bar 一并发布
            for agg in self._aggregators.values():
                bar = agg.flush()
                if bar is not None:
                    await self.bus.publish_bar(_bar_payload(bar, symbol=agg.symbol))
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._consumer, timeout=5)
            except (TimeoutError, asyncio.CancelledError):  # noqa: UP041
                self._consumer.cancel()
            self._consumer = None
        if self._supervisor is not None:
            self._supervisor.cancel()
            self._supervisor = None
        if self._worker is not None and self._worker.is_alive():
            logger.info("终止 CTP worker 子进程 pid=%s", self._worker.pid)
            self._worker.terminate()
            self._worker.join(timeout=5)
        self._worker = None

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
                # 登录成功后订阅（CTP 线程，普通栈帧，避开 asyncio C 栈）
                owner._request_subscribe()

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
        """父进程侧消费：从 worker 子进程队列取消息 → 映射 → 聚合 → 发布。

        用协程轮询（get_nowait + sleep）而非阻塞线程取：tick 量小（rb 每秒
        2-4 笔），轮询开销可忽略，且彻底避开阻塞线程挂住事件循环的坑。
        （聚合与发布都在父进程——worker 崩了不丢 bar 上下文。）
        """
        assert self._mp_queue is not None
        while True:
            try:
                item = self._mp_queue.get_nowait()
            except Empty:
                # 同时处理父进程内部队列（stop 的 sentinel 走这里）
                try:
                    inner = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.05)
                    continue
                if inner is None:
                    return
                continue
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == READY:
                self._ready.set()
            elif kind == DISCONNECTED:
                logger.warning("CTP 连接断开（worker 上报 reason=%s）", item.get("reason"))
            elif kind == TICK:
                await self._on_tick(item.get("data") or {})

    async def _on_tick(self, payload: dict[str, Any]) -> None:
        """tick → 映射 → 聚合 → 发布（原在 spi 回调线程内，现移到父进程）。"""
        contract = str(payload.get("InstrumentID", ""))
        if not contract:
            return
        symbol = self._contract_to_symbol.get(contract, contract)
        tick = mapping.ctp_depth_to_tick(payload, symbol=contract)
        self._tick_count += 1
        if self._tick_count % 20 == 1:
            logger.info(
                "CTP tick 流: %s 第 %d 笔 last=%s", contract, self._tick_count, payload.get("LastPrice")
            )
        agg = self._aggregators.get(symbol)
        if agg is None:
            agg = BarAggregator(symbol, self.interval_minutes)
            self._aggregators[symbol] = agg
        bar = agg.on_tick(tick)
        if bar is not None:
            await self.bus.publish_bar(_bar_payload(bar, symbol=symbol))


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
        self._front_id = 0  # 撤单定位三件套（OnRspUserLogin 记录）
        self._session_id = 0
        self._balance: Decimal = Decimal("0")
        self._positions: list[Any] = []
        self._account_waiter = threading.Event()
        self._deferred: list[dict[str, Any]] = []  # balance 同步轮询时缓存的回报
        # ---- CTP worker 子进程（崩溃隔离，见 worker.py）----
        self._mp_queue: Any = None  # 子→父（回报）
        self._cmd_queue: Any = None  # 父→子（指令）
        self._worker: Any = None
        self._supervisor: asyncio.Task[None] | None = None
        self._worker_lifetime: float | None = None
        self._restart_count = 0
        self._stopping = False

    # ---- OrderPort ----

    def submit(self, command: OrderCommand) -> Order:
        # api 在 worker 子进程内，就绪判据是 worker + READY
        if self._worker is None or not self._ready.is_set():
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
        # 指令经队列下发到 worker 子进程（CTP 调用不在父进程）
        self._send_cmd("submit", values)
        return order

    def _send_cmd(self, action: str, values: dict[str, Any]) -> None:
        if self._cmd_queue is None:
            raise RuntimeError("交易 worker 未启动，请先 await start()")
        self._cmd_queue.put({"action": action, "values": values})

    def cancel(self, order_id: str) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"订单不存在: {order_id}")
        if self._worker is None or not self._ready.is_set():
            raise RuntimeError("交易会话未就绪")
        # 父进程不碰 CTP so（field 构造全在 worker 侧）——纯指令下发，
        # 这也是父进程退出不 segfault 的关键（atexit 析构只存在于子进程）
        values = {
            "BrokerID": self.config.broker_id,
            "InvestorID": self.config.user_id,
            "OrderRef": order_id[-12:],
            "InstrumentID": order.contract,
            "ActionFlag": "0",
            # 撤单定位三件套：FrontID+SessionID+OrderRef（缺前二柜台报"找不到相应报单"）
            "FrontID": self._front_id,
            "SessionID": self._session_id,
        }
        self._send_cmd("cancel", values)
        return order

    def query(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动交易 worker 子进程（崩溃隔离，同 CtpMarketData）。"""
        self._loop = asyncio.get_running_loop()
        if self._worker is None:
            self._start_worker()
            await asyncio.to_thread(self._drain_until_ready, 20)
        self._consumer = asyncio.create_task(self._consume())
        self._supervisor = asyncio.create_task(self._supervise())

    def _start_worker(self) -> None:
        """启动（或重启）交易 worker 子进程。"""
        ctx = multiprocessing.get_context("spawn")
        if self._mp_queue is None:
            self._mp_queue = ctx.Queue()
        if self._cmd_queue is None:
            self._cmd_queue = ctx.Queue()
        self._worker = ctx.Process(
            target=run_ctp_worker,
            args=(self._mp_queue, self._cmd_queue, self.config, [], self._worker_lifetime, "td"),
            name="ctp-td-worker",
            daemon=True,
        )
        self._worker.start()
        logger.info("CTP 交易 worker 子进程启动 pid=%s", self._worker.pid)

    def _drain_until_ready(self, timeout: float) -> None:
        """同步等 worker 的 READY（结算单确认完成）消息。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._ready.is_set():
            try:
                item = self._mp_queue.get(timeout=0.2)
            except Empty:
                continue
            if isinstance(item, dict) and item.get("type") == READY:
                self._front_id = int(item.get("front_id", 0))
                self._session_id = int(item.get("session_id", 0))
                self._ready.set()
                return

    async def _supervise(self) -> None:
        """worker 崩溃/退出 → 重启 → 重启后对账。

        7×24 无人值守：不只是重启进程——重启后必须 sync_state 对齐
        柜台订单状态（崩溃期间的成交/拒单本地不知道），否则仓位/资金
        状态会漂移。
        """
        while True:
            await asyncio.sleep(2)
            if self._stopping or self._worker is None:
                continue
            if not self._worker.is_alive():
                self._restart_count += 1
                logger.warning(
                    "CTP 交易 worker 退出（exitcode=%s），第 %d 次重启", self._worker.exitcode, self._restart_count
                )
                self._ready.clear()
                self._start_worker()
                asyncio.create_task(self._reconcile_after_restart())

    async def _reconcile_after_restart(self) -> None:
        """等新 worker 就绪后自动对账（失败只告警，不阻断监督循环）。"""
        for _ in range(120):
            await asyncio.sleep(1)
            if self._stopping:
                return
            if self._ready.is_set():
                break
        try:
            await self.sync_state()
            logger.info("worker 重启后对账已触发（ReqQryOrder）")
        except RuntimeError as exc:
            logger.warning("重启后对账跳过: %s", exc)

    async def stop(self) -> None:
        self._stopping = True
        if self._consumer is not None:
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._consumer, timeout=5)
            except (TimeoutError, asyncio.CancelledError):  # noqa: UP041
                self._consumer.cancel()
            self._consumer = None
        if self._supervisor is not None:
            self._supervisor.cancel()
            self._supervisor = None
        if self._worker is not None and self._worker.is_alive():
            logger.info("终止 CTP 交易 worker pid=%s", self._worker.pid)
            self._worker.terminate()
            self._worker.join(timeout=5)
        self._worker = None

    # ---- 账户查询（AccountPort 由 CtpAccount 代理到此）----

    def balance_snapshot(self) -> Money:
        """查询可用资金（同步轮询，10 秒超时）。

        balance 是 AccountPort 的同步接口——不能依赖 _consume 协程处理回报
        （同步等待会阻塞事件循环）。故这里同步 drain 队列；遇到的非资金回报
        存入 _deferred，由 _consume 后续处理（不丢回报）。
        """
        if self._worker is None or not self._ready.is_set():
            raise RuntimeError("交易会话未就绪")
        self._send_cmd("query_account", {})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                item = self._mp_queue.get(timeout=0.2)
            except Empty:
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == ACCOUNT:
                self._balance = Decimal(str((item.get("data") or {}).get("Available", "0")))
                return Money(self._balance)
            self._deferred.append(item)  # 委托/成交回报延后处理，不丢
        return Money(self._balance)

    def position_snapshot(self) -> list[Any]:
        """查询持仓（同步轮询，10 秒超时；同 balance_snapshot 的理由）。"""
        if self._worker is None or not self._ready.is_set():
            raise RuntimeError("交易会话未就绪")
        self._positions = []
        self._send_cmd("query_position", {})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                item = self._mp_queue.get(timeout=0.2)
            except Empty:
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == POSITION:
                self._positions.append(item.get("data") or {})
                continue
            self._deferred.append(item)  # 其他回报延后处理
        return list(self._positions)

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def sync_state(self) -> None:
        """重启对账：经 worker 查询柜台订单，回报走域状态机重建本地状态。

        7×24 无人值守的关键一环：worker 崩溃重启后，本地订单状态可能与
        柜台不一致（重启期间成交/拒单未知）——ReqQryOrder 回报以
        OrderRef 匹配本地订单并迁移状态（M1 设计原则：柜台为准）。
        """
        if self._worker is None or not self._ready.is_set():
            raise RuntimeError("交易会话未就绪")
        self._send_cmd(
            "query_order",
            {"BrokerID": self.config.broker_id, "InvestorID": self.config.user_id},
        )

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
                owner._front_id = int(pRspUserLogin.FrontID)
                owner._session_id = int(pRspUserLogin.SessionID)
                # CTP 规范：交易日首次报单前必须确认结算单（快期/vnpy 自动做；
                # 夜盘联调实证：确认链路是报单前置步骤）
                confirm = _to_field(
                    {"BrokerID": owner.config.broker_id, "InvestorID": owner.config.user_id},
                    _load_ctp()[1].CThostFtdcSettlementInfoConfirmField,
                )
                owner._api.ReqSettlementInfoConfirm(confirm, 3)

            def OnRspSettlementInfoConfirm(
                self, pSettlementInfoConfirm: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool
            ) -> None:
                if pRspInfo is not None and pRspInfo.ErrorID != 0:
                    return
                # 结算单确认完成后才解锁 ready（submit 前置条件）
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
        """父进程侧：处理 worker 回报（委托/成交/资金）+ 发布 fill。

        用协程轮询（同 CtpMarketData），避免阻塞线程挂住事件循环。
        """
        assert self._mp_queue is not None
        while True:
            # 先处理 balance 同步轮询时缓存的回报（不丢消息）
            while self._deferred:
                await self._handle_report(self._deferred.pop(0))
            try:
                item = self._mp_queue.get_nowait()
            except Empty:
                try:
                    inner = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.05)
                    continue
                if inner is None:
                    return
                await self.bus.publish("trade.fill", inner)
                continue
            if isinstance(item, dict):
                try:
                    await self._handle_report(item)
                except Exception:  # noqa: BLE001  # 单条回报异常不杀死消费协程
                    logger.exception("回报处理异常: %s", item.get("type"))

    async def _handle_report(self, item: dict[str, Any]) -> None:
        """分发 worker 上报消息。"""
        kind = item.get("type")
        if kind == ORDER:
            self._on_order(item.get("data") or {})
        elif kind == TRADE:
            await self._on_trade(item.get("data") or {})
        elif kind == ACCOUNT:
            self._on_account(item.get("data") or {})
        elif kind == READY:
            self._ready.set()

    def _on_order(self, payload: dict[str, Any]) -> None:
        """委托回报 → 域订单状态机（原在 spi 回调线程内，现移到父进程）。"""
        order = self._match_order(str(payload.get("OrderRef", "")))
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

    async def _on_trade(self, payload: dict[str, Any]) -> None:
        """成交回报 → 域订单部分成交 + fill 发布。"""
        fill = mapping.ctp_trade_to_fill(payload)
        order = self._match_order(str(fill.get("order_ref", "")))
        if order is None:
            return
        qty = Decimal(str(fill.get("quantity", "0")))
        price = Decimal(str(fill.get("price", "0")))
        if qty > 0:
            order.partial_fill(Quantity(qty), price)
        await self.bus.publish(
            "trade.fill",
            {
                "order_id": order.order_id,
                "status": str(order.status),
                "filled_quantity": str(order.filled_quantity),
                "avg_fill_price": str(order.avg_fill_price),
                **fill,
            },
        )

    def _on_account(self, payload: dict[str, Any]) -> None:
        """资金快照回报（balance_snapshot 等待它）。"""
        self._balance = Decimal(str(payload.get("Available", "0")))
        self._account_waiter.set()


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
