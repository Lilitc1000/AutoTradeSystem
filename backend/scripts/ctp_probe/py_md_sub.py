"""实时行情订阅探针（M2-d 前置实弹验证，夜盘窗口）。

用途：验证 SimNow 第一套（仿真）环境的实时行情链路：
登录 → SubscribeMarketData → OnRtnDepthMarketData 收 tick。

用法（容器内，第一套环境）：
    python3 scripts/ctp_probe/py_md_sub.py [tcp://182.254.243.31:30011] [rb2601,hc2601]

凭证从环境变量读取（ats.py exec 自动 source .env）。
"""

from __future__ import annotations

import os
import sys
import time

FRONT = sys.argv[1] if len(sys.argv) > 1 else os.getenv("CTP_MD_FRONT", "tcp://182.254.243.31:30011")
INSTRUMENTS = (sys.argv[2] if len(sys.argv) > 2 else "rb2601").split(",")
BROKER = os.getenv("CTP_BROKER_ID", "9999")
APP_ID = os.getenv("SIMNOW_APP_ID", "simnow_client_test")
AUTH_CODE = os.getenv("SIMNOW_AUTH_CODE", "0000000000000000")
USER = os.getenv("SIMNOW_USER_ID", "")
PASSWORD = os.getenv("SIMNOW_PASSWORD", "")


def main() -> int:
    if not USER or not PASSWORD:
        print("缺少 SIMNOW_USER_ID / SIMNOW_PASSWORD")
        return 2

    from openctp_ctp.thostmduserapi import (
        CThostFtdcMdApi,
        CThostFtdcMdSpi,
        CThostFtdcReqUserLoginField,
    )

    api = CThostFtdcMdApi.CreateFtdcMdApi("./flow/")
    ticks: list = []
    sub_results: list = []

    class Impl(CThostFtdcMdSpi):
        def __init__(self) -> None:
            super().__init__()
            self.login_done = False
            self.login_ok = False

        def OnFrontConnected(self) -> None:
            print(f"[md-sub] 前置连接成功 {FRONT}", flush=True)
            # 注意：md api 无 Authenticate（那是 td api 的看穿式认证，见 td_demo）；
            # 行情流程：连接 → ReqUserLogin → 订阅
            f = CThostFtdcReqUserLoginField()
            f.BrokerID = BROKER
            f.UserID = USER
            f.Password = PASSWORD
            api.ReqUserLogin(f, 1)

        def OnFrontDisconnected(self, reason: int) -> None:
            print(f"[md-sub] 前置断开 reason={reason}", flush=True)

        def OnRspUserLogin(self, pRspUserLogin, pRspInfo, request_id: int, is_last: bool) -> None:
            self.login_done = True
            if pRspInfo is not None and pRspInfo.ErrorID != 0:
                print(f"[md-sub] 登录失败 {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)
                return
            self.login_ok = True
            day = pRspUserLogin.TradingDay if pRspUserLogin else "?"
            print(f"[md-sub] 登录成功 TradingDay={day}", flush=True)
            # openctp 6.7.11 的 char*[] typemap 要 list of bytes（官方 demo 同款）
            for inst in INSTRUMENTS:
                api.SubscribeMarketData([inst.encode("utf-8")], 1)

        def OnRspSubMarketData(self, pSpecificInstrument, pRspInfo, request_id: int, is_last: bool) -> None:
            inst = pSpecificInstrument.InstrumentID if pSpecificInstrument else "?"
            if pRspInfo is not None and pRspInfo.ErrorID != 0:
                print(f"[md-sub] 订阅 {inst} 失败 {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)
                sub_results.append((inst, False))
            else:
                print(f"[md-sub] 订阅 {inst} 成功", flush=True)
                sub_results.append((inst, True))

        def OnRtnDepthMarketData(self, pDepthMarketData) -> None:
            if pDepthMarketData is None:
                return
            ticks.append(pDepthMarketData)
            if len(ticks) <= 5:
                print(
                    f"[md-sub] tick {pDepthMarketData.InstrumentID} "
                    f"last={pDepthMarketData.LastPrice} vol={pDepthMarketData.Volume} "
                    f"time={pDepthMarketData.UpdateTime}.{pDepthMarketData.UpdateMillisec}",
                    flush=True,
                )

        def OnRspError(self, pRspInfo, request_id: int, is_last: bool) -> None:
            if pRspInfo is not None:
                print(f"[md-sub] OnRspError {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)

    impl = Impl()
    api.RegisterSpi(impl)
    api.RegisterFront(FRONT)
    print(f"[md-sub] BrokerID={BROKER} UserID={USER} Front={FRONT} 订阅={INSTRUMENTS}", flush=True)
    api.Init()

    # 最多收 45 秒（够夜盘多个 tick）
    deadline = time.time() + 90
    while time.time() < deadline:
        if len(ticks) >= 20:
            break
        time.sleep(0.2)

    print(f"[md-sub] 订阅结果: {sub_results}", flush=True)
    print(f"[md-sub] 共收到 {len(ticks)} 个 tick", flush=True)
    if ticks:
        last = ticks[-1]
        print(
            f"[md-sub] 最后 tick: {last.InstrumentID} last={last.LastPrice} "
            f"bid1={last.BidPrice1} ask1={last.AskPrice1} vol={last.Volume} "
            f"oi={last.OpenInterest} time={last.UpdateTime}.{last.UpdateMillisec}",
            flush=True,
        )
    api.Release()
    ok = bool(sub_results) and all(ok for _, ok in sub_results) and len(ticks) > 0
    print(f"[md-sub] {'PASS' if ok else 'FAIL'}: 实时行情链路{'通' if ok else '不通'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
