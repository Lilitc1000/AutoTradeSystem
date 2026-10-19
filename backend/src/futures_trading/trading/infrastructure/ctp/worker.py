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
from typing import Any

logger = logging.getLogger(__name__)

# 队列消息类型
READY = "ready"
TICK = "tick"
DISCONNECTED = "disconnected"


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


def _snapshot(field: Any) -> dict[str, Any]:
    """行情字段 → 纯 Python 字典（可 pickle，供跨进程队列传输）。

    显式取值而非通用反射：通用转换会带回 SwigPyObject（无法 pickle）。
    """
    out: dict[str, Any] = {}
    for name in _FIELDS:
        value = getattr(field, name, None)
        if value is None:
            continue
        out[name] = int(value) if name in ("UpdateMillisec",) else str(value)
    return out


def run_ctp_worker(
    q: Any,
    config: Any,
    contracts: list[str],
    lifetime: float | None = None,
) -> None:
    """子进程入口（模块级函数，spawn 可 pickle）。

    q：multiprocessing.Queue，向父进程推送 {"type": ..., ...}
    config：CtpConfig（可 pickle）
    contracts：订阅合约列表（合约代码，如 rb2701）
    lifetime：存活秒数（None=常驻）；到期主动退出，父进程重启即内存重置
    """
    import gc

    gc.disable()  # 约束 1

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

    api = CThostFtdcMdApi.CreateFtdcMdApi(config.flow_path("flow_md"))
    spi = _Spi()  # 持引用（否则被 GC 后回调丢失）
    api.RegisterSpi(spi)
    api.RegisterFront(config.md_front)
    api.Init()
    logger.info("[ctp-worker] 已启动（pid 独立进程）contracts=%s", contracts)

    deadline = time.time() + lifetime if lifetime else None
    try:
        while True:
            if deadline is not None and time.time() >= deadline:
                logger.info("[ctp-worker] 存活期到，主动退出（内存重置）")
                break
            time.sleep(0.2)
    finally:
        api.Release()
        logger.info("[ctp-worker] 已退出")
