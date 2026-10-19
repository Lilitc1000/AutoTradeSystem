"""报单探针（M2 SIM 报单链路验证）：认证 → 登录 → 限价挂单 → 回报 → 撤单。

用法（容器内）：python3 scripts/ctp_probe/py_order_probe.py [合约] [价格]
  合约默认 rb2701；价格默认 2950（远离市价 ~3084，挂单不成交，可安全撤掉）
流程（官方 td_demo 同款）：
  OnFrontConnected → ReqAuthenticate → ReqUserLogin →
  ReqOrderInsert(限价) → OnRtnOrder/OnRtnTrade → ReqOrderAction(撤单)
凭证从环境变量读取（exec 时 source .env）。
"""

from __future__ import annotations

import os
import sys
import time

FRONT = "tcp://182.254.243.31:30001"  # 第一套交易前置
BROKER = os.getenv("CTP_BROKER_ID", "9999")
USER = os.getenv("SIMNOW_USER_ID", "")
PASSWORD = os.getenv("SIMNOW_PASSWORD", "")
APP_ID = os.getenv("SIMNOW_APP_ID", "simnow_client_test")
AUTH_CODE = os.getenv("SIMNOW_AUTH_CODE", "0000000000000000")
CONTRACT = sys.argv[1] if len(sys.argv) > 1 else "rb2701"
PRICE = sys.argv[2] if len(sys.argv) > 2 else "2950"


def main() -> int:
    if not USER or not PASSWORD:
        print("缺少 SIMNOW_USER_ID / SIMNOW_PASSWORD")
        return 2

    import gc

    gc.disable()  # openctp SWIG + GC segfault 规避（见 adapters.create docstring）
    os.makedirs("./flow_td_probe/", exist_ok=True)

    from openctp_ctp.thosttraderapi import (
        CThostFtdcReqAuthenticateField,
        CThostFtdcReqUserLoginField,
        CThostFtdcInputOrderActionField,
        CThostFtdcInputOrderField,
        CThostFtdcTraderApi,
        CThostFtdcTraderSpi,
    )

    api = CThostFtdcTraderApi.CreateFtdcTraderApi("./flow_td_probe/")
    state = {"login": False, "order_ref": "", "submitted": False, "traded": False, "cancelled": False}

    class Impl(CThostFtdcTraderSpi):
        def OnFrontConnected(self) -> None:
            print("[order-probe] 前置连接成功", flush=True)
            auth = CThostFtdcReqAuthenticateField()
            auth.BrokerID = BROKER
            auth.UserID = USER
            auth.AppID = APP_ID
            auth.AuthCode = AUTH_CODE
            api.ReqAuthenticate(auth, 1)

        def OnRspAuthenticate(self, pRspAuthenticateField, pRspInfo, nRequestID, bIsLast) -> None:
            if pRspInfo and pRspInfo.ErrorID != 0:
                print(f"[order-probe] 认证失败 {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)
                return
            print("[order-probe] 认证成功（看穿式）", flush=True)
            f = CThostFtdcReqUserLoginField()
            f.BrokerID = BROKER
            f.UserID = USER
            f.Password = PASSWORD
            api.ReqUserLogin(f, 2)

        def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast) -> None:
            if pRspInfo and pRspInfo.ErrorID != 0:
                print(f"[order-probe] 登录失败 {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)
                return
            state["login"] = True
            print(f"[order-probe] 登录成功 TradingDay={pRspUserLogin.TradingDay}", flush=True)
            # 限价开多 1 手（价格远离市价，不会成交）
            order = CThostFtdcInputOrderField()
            order.BrokerID = BROKER
            order.InvestorID = USER
            order.UserID = USER
            order.InstrumentID = CONTRACT
            order.OrderRef = "900001"
            order.Direction = "0"  # 多
            order.CombOffsetFlag = "0"  # 开
            order.CombHedgeFlag = "1"  # 投机
            order.OrderPriceType = "1"  # 限价
            order.LimitPrice = float(PRICE)
            order.VolumeTotalOriginal = 1
            order.TimeCondition = "3"  # 当日有效
            order.VolumeCondition = "1"
            order.ContingentCondition = "1"
            order.ForceCloseReason = "0"
            order.MinVolume = 1
            print(f"[order-probe] 发限价单 {CONTRACT} 开多 1手 @ {PRICE}", flush=True)
            api.ReqOrderInsert(order, 3)

        def OnRtnOrder(self, pOrder) -> None:
            status = pOrder.OrderStatus
            submit_status = pOrder.OrderSubmitStatus
            print(
                f"[order-probe] 委托回报 OrderRef={pOrder.OrderRef} "
                f"OrderStatus={status} OrderSubmitStatus={submit_status} "
                f"StatusMsg={pOrder.StatusMsg}",
                flush=True,
            )
            if submit_status == "0" and status == "2":
                state["submitted"] = True
            if status == "5":  # 已撤
                state["cancelled"] = True

        def OnRtnTrade(self, pTrade) -> None:
            state["traded"] = True
            print(
                f"[order-probe] 成交回报 {pTrade.InstrumentID} "
                f"vol={pTrade.Volume} price={pTrade.Price} TradeID={pTrade.TradeID}",
                flush=True,
            )

        def OnErrRtnOrderInsert(self, pInputOrder, pRspInfo) -> None:
            if pRspInfo:
                print(f"[order-probe] 报单错误 {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)

        def OnRspOrderAction(self, pInputOrderAction, pRspInfo, nRequestID, bIsLast) -> None:
            if pRspInfo and pRspInfo.ErrorID != 0:
                print(f"[order-probe] 撤单失败 {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)
            else:
                print("[order-probe] 撤单请求已接受", flush=True)

        def OnRspError(self, pRspInfo, nRequestID, bIsLast) -> None:
            if pRspInfo:
                print(f"[order-probe] OnRspError {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)

    api.RegisterSpi(Impl())
    api.RegisterFront(FRONT)
    api.SubscribePrivateTopic(2)
    api.SubscribePublicTopic(2)
    api.Init()

    # 等登录+报单回报（最多 15 秒）
    for _ in range(150):
        if state["login"]:
            break
        time.sleep(0.1)
    if not state["login"]:
        print("[order-probe] 登录超时")
        api.Release()
        return 1

    # 等委托回报
    for _ in range(50):
        if state["submitted"] or state["traded"]:
            break
        time.sleep(0.1)

    # 撤单（无论是否成交都撤——成交的单撤不掉但无妨）
    print("[order-probe] 撤单 ...", flush=True)
    action = CThostFtdcInputOrderActionField()
    action.BrokerID = BROKER
    action.InvestorID = USER
    action.UserID = USER
    action.InstrumentID = CONTRACT
    action.OrderRef = "900001"
    action.ActionFlag = "0"
    api.ReqOrderAction(action, 4)

    for _ in range(50):
        if state["cancelled"]:
            break
        time.sleep(0.1)

    time.sleep(1)
    api.Release()
    ok = state["submitted"] or state["traded"]
    print(
        f"[order-probe] {'PASS' if ok else 'FAIL'}: "
        f"login={state['login']} submitted={state['submitted']} "
        f"traded={state['traded']} cancelled={state['cancelled']}",
        flush=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
