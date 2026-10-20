"""CTP worker 子进程：只跑 CTP so + SWIG（裸 Python、禁 GC、无 asyncio）。

为什么独立进程（vnpy 架构）：
- openctp 6.7.11 的 SWIG 与 Python GC 交互会偶发 segfault（M2-d gdb 实锤，
  夜盘实测崩率 ~50%）——崩溃发生在 C++ 扩展侧，同进程会带走整个 worker
  （聚合状态、Redis 连接、心跳全丢）。
- 隔离后：worker 崩了父进程（md_gateway）重启它即可，父侧业务状态无感。

约束（均实证，勿改）：
1. gc.disable()——SWIG + GC 竞争是崩因；
2. **SWIG field 对象必须在主线程预创建**（回调线程内构造会 segfault，
   夜盘 21:19 faulthandler 实锤）；
3. 订阅用 list[bytes]（openctp char*[] typemap）；
4. spawn 上下文（fork 会继承 C++ 扩展状态，更危险）。
"""

from __future__ import annotations

import logging
import threading
import time
from queue import Empty
from typing import Any

logger = logging.getLogger(__name__)

# 队列消息类型
READY = "ready"
TICK = "tick"
DISCONNECTED = "disconnected"
ORDER = "order"  # 委托回报（子→父）
TRADE = "trade"  # 成交回报（子→父）
ACCOUNT = "account"  # 资金快照（子→父）
POSITION = "position"  # 持仓快照（子→父）


_FIELDS = (
    "InstrumentID",
    "TradingDay",
    "UpdateTime",
    "UpdateMillisec",
    "LastPrice",
    "Volume",
    "OpenInterest",
    "BidPrice1",
    "AskPrice1",
    "BidVolume1",
    "AskVolume1",
    "Turnover",
)


def _snapshot(field: Any, fields: tuple[str, ...] = _FIELDS) -> dict[str, Any]:
    """行情/回报字段 → 纯 Python 字典（可 pickle，供跨进程队列传输）。

    显式取值而非通用反射：通用转换会带回 SwigPyObject（无法 pickle）。
    """
    out: dict[str, Any] = {}
    for name in fields:
        value = getattr(field, name, None)
        if value is None:
            continue
        out[name] = int(value) if name in ("UpdateMillisec",) else str(value)
    return out


_ORDER_FIELDS = (
    "OrderRef",
    "InstrumentID",
    "OrderStatus",
    "OrderSubmitStatus",
    "StatusMsg",
    "VolumeTotalOriginal",
    "VolumeTraded",
    "Direction",
    "CombOffsetFlag",
    "LimitPrice",
    "FrontID",
    "SessionID",
)
_TRADE_FIELDS = (
    "OrderRef",
    "InstrumentID",
    "TradeID",
    "Volume",
    "Price",
    "Direction",
    "TradeDate",
    "TradeTime",
)
_ACCOUNT_FIELDS = ("Available", "Balance", "CurrMargin", "PositionProfit", "CloseProfit")
_POSITION_FIELDS = (
    "InstrumentID",
    "Direction",
    "Position",
    "YdPosition",
    "TodayPosition",
    "PositionCost",
    "UseMargin",
)


def _assign(field: Any, values: dict[str, Any]) -> None:
    """把指令字段写入预创建的 SWIG field（回调/指令线程内不构造新对象）。"""
    for key, value in values.items():
        setattr(field, key, value)


def _handle_cmd(
    api: Any,
    cmd: dict[str, Any],
    order_field: Any,
    action_field: Any,
    acct_field: Any,
    pos_field: Any,
    qry_order_field: Any,
) -> None:
    """执行父进程下发的指令（submit/cancel/query_account）。"""
    state = _CMD_STATE
    state["req_id"] += 1
    action = cmd.get("action")
    values = cmd.get("values") or {}
    if action == "submit":
        _assign(order_field, values)
        logger.info("[ctp-worker-td] 发单 %s", values.get("InstrumentID"))
        api.ReqOrderInsert(order_field, state["req_id"])
    elif action == "cancel":
        _assign(action_field, values)
        api.ReqOrderAction(action_field, state["req_id"])
    elif action == "query_account":
        api.ReqQryTradingAccount(acct_field, state["req_id"])
    elif action == "query_position":
        api.ReqQryInvestorPosition(pos_field, state["req_id"])
    elif action == "query_order":
        api.ReqQryOrder(qry_order_field, state["req_id"])


_CMD_STATE: dict[str, int] = {"req_id": 0}


def _clean_flow(config: Any, name: str) -> str:
    """清残留 flow 目录后重建：崩溃/被杀留下的 session 文件会让
    CTP CreateFtdcXxxApi/Init 挂起（实踩：日盘复现 worker 无任何输出）。"""
    import os
    import shutil

    path = os.path.join(config.flow_dir, name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    return path


def _run_td(q: Any, cmd_q: Any, config: Any, lifetime: float | None) -> None:
    """交易子进程：认证 → 登录 → 结算单确认 → 指令循环。"""
    from openctp_ctp.thosttraderapi import (
        CThostFtdcInputOrderActionField,
        CThostFtdcInputOrderField,
        CThostFtdcQryInvestorPositionField,
        CThostFtdcQryOrderField,
        CThostFtdcQryTradingAccountField,
        CThostFtdcReqAuthenticateField,
        CThostFtdcReqUserLoginField,
        CThostFtdcSettlementInfoConfirmField,
        CThostFtdcTraderApi,
        CThostFtdcTraderSpi,
    )

    # 约束 2：全部预创建（回调线程内构造 SWIG 对象会 segfault）
    auth_field = CThostFtdcReqAuthenticateField()
    auth_field.BrokerID = config.broker_id
    auth_field.UserID = config.user_id
    auth_field.AppID = config.app_id
    auth_field.AuthCode = config.auth_code
    login_field = CThostFtdcReqUserLoginField()
    login_field.BrokerID = config.broker_id
    login_field.UserID = config.user_id
    login_field.Password = config.password
    confirm_field = CThostFtdcSettlementInfoConfirmField()
    confirm_field.BrokerID = config.broker_id
    confirm_field.InvestorID = config.user_id
    order_field = CThostFtdcInputOrderField()
    action_field = CThostFtdcInputOrderActionField()
    acct_field = CThostFtdcQryTradingAccountField()
    acct_field.BrokerID = config.broker_id
    acct_field.InvestorID = config.user_id
    pos_field = CThostFtdcQryInvestorPositionField()
    pos_field.BrokerID = config.broker_id
    pos_field.InvestorID = config.user_id
    qry_order_field = CThostFtdcQryOrderField()
    qry_order_field.BrokerID = config.broker_id
    qry_order_field.InvestorID = config.user_id

    api: Any = None

    class _TdSpi(CThostFtdcTraderSpi):
        def OnFrontConnected(self) -> None:
            logger.info("[ctp-worker-td] 前置连接成功 %s", config.trade_front)
            api.ReqAuthenticate(auth_field, 1)

        def OnRspAuthenticate(self, pAuth: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
            if pRspInfo is not None and pRspInfo.ErrorID != 0:
                logger.error("[ctp-worker-td] 认证失败 %s: %s", pRspInfo.ErrorID, pRspInfo.ErrorMsg)
                return
            logger.info("[ctp-worker-td] 认证成功（看穿式）")
            api.ReqUserLogin(login_field, 2)

        def OnRspUserLogin(self, pLogin: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
            if pRspInfo is not None and pRspInfo.ErrorID != 0:
                logger.error("[ctp-worker-td] 登录失败 %s: %s", pRspInfo.ErrorID, pRspInfo.ErrorMsg)
                return
            _CMD_STATE["front_id"] = int(getattr(pLogin, "FrontID", 0))
            _CMD_STATE["session_id"] = int(getattr(pLogin, "SessionID", 0))
            logger.info("[ctp-worker-td] 登录成功 TradingDay=%s", getattr(pLogin, "TradingDay", "?"))
            api.ReqSettlementInfoConfirm(confirm_field, 3)

        def OnRspSettlementInfoConfirm(self, pConfirm: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
            if pRspInfo is not None and pRspInfo.ErrorID != 0:
                logger.error("[ctp-worker-td] 结算单确认失败 %s: %s", pRspInfo.ErrorID, pRspInfo.ErrorMsg)
                return
            logger.info("[ctp-worker-td] 结算单已确认")
            q.put(
                {
                    "type": READY,
                    "front_id": _CMD_STATE["front_id"],
                    "session_id": _CMD_STATE["session_id"],
                }
            )

        def OnRtnOrder(self, pOrder: Any) -> None:
            q.put({"type": ORDER, "data": _snapshot(pOrder, _ORDER_FIELDS)})

        def OnRtnTrade(self, pTrade: Any) -> None:
            q.put({"type": TRADE, "data": _snapshot(pTrade, _TRADE_FIELDS)})

        def OnRspQryTradingAccount(self, pAccount: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
            if pAccount is not None:
                q.put({"type": ACCOUNT, "data": _snapshot(pAccount, _ACCOUNT_FIELDS)})

        def OnRspQryOrder(self, pOrder: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
            """对账回报：经队列回传父进程，走域订单状态机重建本地状态。"""
            if pOrder is not None:
                q.put({"type": ORDER, "data": _snapshot(pOrder, _ORDER_FIELDS)})

        def OnRspQryInvestorPosition(self, pPosition: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
            if pPosition is not None:
                q.put({"type": POSITION, "data": _snapshot(pPosition, _POSITION_FIELDS)})

        def OnFrontDisconnected(self, nReason: int) -> None:
            logger.warning("[ctp-worker-td] 连接断开 reason=%s", nReason)
            q.put({"type": DISCONNECTED, "reason": int(nReason)})

    api = CThostFtdcTraderApi.CreateFtdcTraderApi(_clean_flow(config, "flow_td"))
    spi = _TdSpi()  # 持引用
    api.RegisterSpi(spi)
    api.RegisterFront(config.trade_front)
    api.SubscribePrivateTopic(2)
    api.SubscribePublicTopic(2)
    api.Init()
    logger.info("[ctp-worker-td] 已启动")

    deadline = time.time() + lifetime if lifetime else None
    stop = threading.Event()
    _install_signal_handlers(stop)
    try:
        while True:
            if stop.is_set():
                logger.info("[ctp-worker-td] 收到停止信号，优雅退出")
                break
            if deadline is not None and time.time() >= deadline:
                logger.info("[ctp-worker-td] 存活期到，主动退出")
                break
            try:
                cmd = cmd_q.get_nowait()
            except Empty:
                time.sleep(0.05)
                continue
            _handle_cmd(api, cmd, order_field, action_field, acct_field, pos_field, qry_order_field)
    finally:
        api.Release()
        logger.info("[ctp-worker-td] 已退出")


def run_ctp_worker(
    q: Any,
    cmd_q: Any,
    config: Any,
    contracts: list[str],
    lifetime: float | None = None,
    mode: str = "md",
) -> None:
    """子进程入口（模块级函数，spawn 可 pickle）。

    q：子→父队列；cmd_q：父→子指令队列（td 模式用）
    mode："md"=行情，"td"=交易（认证链 + 指令循环）
    """
    # 子进程入口：配置日志（spawn 子进程不继承父进程的 logging 配置）
    # 注意：**不要**在这里 logging.basicConfig——实测会让 CTP 初始化
    # 阶段 SIGSEGV（exitcode=-11，无任何日志）。worker 状态靠队列回报，
    # 日志配置由父进程（spawn import 主模块时）继承。

    if mode == "td":
        # 约束 1 的 gc.disable() **不能用于 td**：实测会让子进程在 CTP
        # 初始化前 SIGSEGV（exitcode=-11，无任何日志）——该约束是当初为
        # md worker 的 SWIG 析构崩溃加的，只对 md 生效。
        _run_td(q, cmd_q, config, lifetime)
        return

    import gc

    gc.disable()  # 约束 1（md 专用：SWIG 对象析构竞态）
    _run_md(q, config, contracts, lifetime)


def _install_signal_handlers(stop: Any) -> None:
    """SIGTERM/SIGINT 优雅停机：置停止标志让主循环走 finally Release()。

    没有这个，compose stop/父进程 terminate 时 daemon 立即死，CTP so
    来不及析构——segfault + 柜台侧连接非优雅关闭（保留 1-2 分钟，
    阻塞下次登录）。7×24 无人值守的基本要求。
    """
    import signal

    def _handler(signum: int, _frame: Any) -> None:
        logger.info("收到信号 %s，优雅停机中", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _run_md(q: Any, config: Any, contracts: list[str], lifetime: float | None) -> None:

    from openctp_ctp.thostmduserapi import (
        CThostFtdcMdApi,
        CThostFtdcMdSpi,
        CThostFtdcReqUserLoginField,
    )


    logged_in = threading.Event()
    # 约束 2：field 对象预创建（回调线程内构造会 segfault）
    login_field = CThostFtdcReqUserLoginField()
    login_field.BrokerID = config.broker_id
    login_field.UserID = config.user_id
    login_field.Password = config.password

    api: Any = None

    class _Spi(CThostFtdcMdSpi):
        def OnFrontConnected(self) -> None:
            logger.info("[ctp-worker] 前置连接成功 %s", config.md_front)
            api.ReqUserLogin(login_field, 1)

        def OnRspUserLogin(self, pRspUserLogin: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
            if pRspInfo is not None and pRspInfo.ErrorID != 0:
                logger.error("[ctp-worker] 登录失败 %s: %s", pRspInfo.ErrorID, pRspInfo.ErrorMsg)
                return
            logged_in.set()
            logger.info("[ctp-worker] 登录成功 TradingDay=%s", getattr(pRspUserLogin, "TradingDay", "?"))
            q.put({"type": READY, "trading_day": str(getattr(pRspUserLogin, "TradingDay", ""))})
            # 约束 3：bytes list
            encoded = [c.encode("utf-8") for c in contracts]
            api.SubscribeMarketData(encoded, len(encoded))
            logger.info("[ctp-worker] 已订阅 %s", contracts)

        def OnRspSubMarketData(self, pSpecific: Any, pRspInfo: Any, nRequestID: int, bIsLast: bool) -> None:
            if pRspInfo is not None and pRspInfo.ErrorID != 0:
                logger.error("[ctp-worker] 订阅失败 %s: %s", pRspInfo.ErrorID, pRspInfo.ErrorMsg)

        def OnRtnDepthMarketData(self, pDepthMarketData: Any) -> None:
            # 必须显式提取为纯 Python 值：_field_to_dict 可能带回 SwigPyObject，
            # multiprocessing.Queue 无法 pickle（实踩 TypeError）
            q.put({"type": TICK, "data": _snapshot(pDepthMarketData)})

        def OnFrontDisconnected(self, nReason: int) -> None:
            logger.warning("[ctp-worker] 连接断开 reason=%s", nReason)
            q.put({"type": DISCONNECTED, "reason": int(nReason)})

    api = CThostFtdcMdApi.CreateFtdcMdApi(_clean_flow(config, "flow_md"))
    spi = _Spi()  # 持引用（否则被 GC 后回调丢失）
    api.RegisterSpi(spi)
    api.RegisterFront(config.md_front)
    api.Init()
    logger.info("[ctp-worker] 已启动（pid 独立进程）contracts=%s", contracts)

    deadline = time.time() + lifetime if lifetime else None
    stop = threading.Event()
    _install_signal_handlers(stop)
    try:
        while True:
            if stop.is_set():
                logger.info("[ctp-worker] 收到停止信号，优雅退出")
                break
            if deadline is not None and time.time() >= deadline:
                logger.info("[ctp-worker] 存活期到，主动退出（内存重置）")
                break
            time.sleep(0.2)
    finally:
        api.Release()
        logger.info("[ctp-worker] 已退出")
