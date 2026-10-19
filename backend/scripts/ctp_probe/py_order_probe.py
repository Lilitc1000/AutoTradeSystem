"""报单探针（M2 SIM 报单链路验证）：认证 → 登录 → 限价挂单 → 回报 → 撤单。

用法：python3 py_order_probe.py [合约] [价格]
  合约默认 rb2701；价格默认 2950（远离市价 ~3084，挂单不成交，可安全撤掉）
  平台：Linux 容器 / Windows 宿主机均可（WSL2 容器缺 DMI 表无法交易登录，
  Windows 宿主机有机器码——Windows 直跑是 WSL2 环境的报单验证路径）
流程（官方 td_demo 同款）：
  OnFrontConnected → ReqAuthenticate → ReqUserLogin →
  ReqOrderInsert(限价) → OnRtnOrder/OnRtnTrade → ReqOrderAction(撤单)
凭证：环境变量优先；为空时从 .devcontainer/.env 加载（Windows 宿主机直跑用）。
注意：Windows 宿主机跑本探针必须复制到本地路径（UNC 路径 + CTP so 会
  segfault，实测：本地路径同代码完全正常）；午休/非交易时段交易登录会返回
  "CTP:用户不活跃"（错误码4，vnpy 社区同款案例，交易时段即恢复）。
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import time

FRONT = os.getenv("ORDER_PROBE_FRONT", "tcp://182.254.243.31:30001")  # 第一套交易前置


def _load_env() -> None:
    """环境变量缺凭证时，从 .devcontainer/.env 补全（仅 setdefault，不覆盖）。"""
    if os.getenv("SIMNOW_USER_ID"):
        return
    for path in (
        "/workspace/.devcontainer/.env",  # 容器内
        "//wsl.localhost/Ubuntu/home/autotoll/repos/AutoTradeSystem/.devcontainer/.env",
    ):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        os.environ.setdefault(key.strip(), value.strip())
            print(f"[order-probe] 凭证已从 {path} 加载", flush=True)
            return


_load_env()

BROKER = os.getenv("CTP_BROKER_ID", "9999")
USER = os.getenv("SIMNOW_USER_ID", "")
PASSWORD = os.getenv("SIMNOW_PASSWORD", "")
APP_ID = os.getenv("SIMNOW_APP_ID", "simnow_client_test")
AUTH_CODE = os.getenv("SIMNOW_AUTH_CODE", "0000000000000000")
CONTRACT = sys.argv[1] if len(sys.argv) > 1 else "rb2701"
PRICE = sys.argv[2] if len(sys.argv) > 2 else "2950"
# flow 目录：Linux 用相对路径（容器 cwd 本地层）；Windows 用系统临时目录
FLOW_DIR = (
    "./flow_td_probe/" if platform.system() == "Linux" else os.path.join(tempfile.gettempdir(), "flow_td_probe")
)


def main() -> int:
    if not USER or not PASSWORD:
        print("缺少 SIMNOW_USER_ID / SIMNOW_PASSWORD")
        return 2

    import gc

    gc.disable()  # openctp SWIG + GC segfault 规避（见 adapters.create docstring）
    os.makedirs(FLOW_DIR, exist_ok=True)

    from openctp_ctp.thosttraderapi import (
        CThostFtdcQryOrderField,
        CThostFtdcReqAuthenticateField,
        CThostFtdcReqUserLoginField,
        CThostFtdcInputOrderActionField,
        CThostFtdcInputOrderField,
        CThostFtdcTraderApi,
        CThostFtdcTraderSpi,
    )

    api = CThostFtdcTraderApi.CreateFtdcTraderApi(FLOW_DIR)
    state = {"login": False, "order_ref": "", "submitted": False, "traded": False, "cancelled": False}
    # SWIG field 对象全部预创建（主线程）：faulthandler 实证回调线程内构造
    # （OnRspAuthenticate → CThostFtdcReqUserLoginField）segfault——与
    # CreateFtdcMdApi 的 GC/线程敏感同源。回调里只赋值+调用。
    state["auth_field"] = CThostFtdcReqAuthenticateField()
    state["login_field"] = CThostFtdcReqUserLoginField()
    state["order_field"] = CThostFtdcInputOrderField()
    state["action_field"] = CThostFtdcInputOrderActionField()
    state["qry_field"] = CThostFtdcQryOrderField()

    class Impl(CThostFtdcTraderSpi):
        def OnFrontConnected(self) -> None:
            print("[order-probe] 前置连接成功", flush=True)
            auth = state["auth_field"]  # 预创建（回调线程构造 SWIG 对象会崩）
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
            f = state["login_field"]  # 预创建
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
            # 限价开多 1 手（价格略低于市价 ~3084，挂住不成交；远离市价会被
            # SimNow 价格保护自动撤单，实证 2950 时 a→5 直接被清）
            order = state["order_field"]  # 预创建
            order.BrokerID = BROKER
            order.InvestorID = USER
            order.UserID = USER
            order.InstrumentID = CONTRACT
            # OrderRef 唯一化（时间戳后6位）：固定值跨进程/跨轮次会撞单
            order_ref = str(int(time.time()) % 1000000)
            order.OrderRef = order_ref
            state["order_ref"] = order_ref
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

        def OnRspOrderInsert(self, pInputOrder, pRspInfo, nRequestID, bIsLast) -> None:
            if pRspInfo and pRspInfo.ErrorID != 0:
                print(f"[order-probe] 发单被拒 {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)
            else:
                print("[order-probe] 发单请求已被柜台接受", flush=True)

        def OnRspQryOrder(self, pOrder, pRspInfo, nRequestID, bIsLast) -> None:
            if pOrder:
                print(
                    f"[order-probe] 委托记录 Ref={pOrder.OrderRef} Status={pOrder.OrderStatus} "
                    f"Submit={pOrder.OrderSubmitStatus} Msg={pOrder.StatusMsg}",
                    flush=True,
                )

        def OnRspOrderAction(self, pInputOrderAction, pRspInfo, nRequestID, bIsLast) -> None:
            if pRspInfo and pRspInfo.ErrorID != 0:
                print(f"[order-probe] 撤单失败 {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)
            else:
                print("[order-probe] 撤单请求已接受", flush=True)

        def OnRspError(self, pRspInfo, nRequestID, bIsLast) -> None:
            if pRspInfo:
                print(f"[order-probe] OnRspError {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)

    # spi 必须持有引用：inline Impl() 会被 GC，SWIG director 回调到已毁对象
    # （Windows 实测 AttributeError / Linux 实测堆损坏——M2-d 同款坑）
    spi = Impl()
    api.RegisterSpi(spi)
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

    # 等委托回报（8 秒：订单从"尚未申报"到"已委托"需时间，5 秒曾太短）
    for _ in range(80):
        if state["submitted"] or state["traded"]:
            break
        time.sleep(0.1)

    # 撤单（无论是否成交都撤——成交的单撤不掉但无妨）
    print("[order-probe] 撤单 ...", flush=True)
    action = state["action_field"]  # 预创建
    action.BrokerID = BROKER
    action.InvestorID = USER
    action.UserID = USER
    action.InstrumentID = CONTRACT
    action.OrderRef = state["order_ref"]  # 与发单同一 OrderRef
    action.ActionFlag = "0"
    api.ReqOrderAction(action, 4)

    for _ in range(50):
        if state["cancelled"]:
            break
        time.sleep(0.1)

    # 查当日委托（StatusMsg 会写明订单被撤的原因——挖 a→5 的直通道）
    print("[order-probe] 查询当日委托 ...", flush=True)
    qry = state["qry_field"]
    qry.BrokerID = BROKER
    qry.InvestorID = USER
    api.ReqQryOrder(qry, 5)
    time.sleep(2)

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
