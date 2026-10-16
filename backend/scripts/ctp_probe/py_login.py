"""Python 版 CTP 登录探针（验证 openctp_ctp Python 绑定 + SimNow 账号）。

用法（容器内）：
    python3 scripts/ctp_probe/py_login.py [FrontAddr]

依赖：openctp_ctp（已打 zh_CN.GB18030→C patch，见 patch_gbk.sh）。
凭证从环境变量读取（ats.py exec 自动 source .env）。
"""

from __future__ import annotations

import os
import sys
import time

FRONT = sys.argv[1] if len(sys.argv) > 1 else os.getenv(
    "CTP_MD_FRONT", "tcp://182.254.243.31:40011"
)
BROKER = os.getenv("CTP_BROKER_ID", "9999")
USER = os.getenv("SIMNOW_USER_ID", "")
PASSWORD = os.getenv("SIMNOW_PASSWORD", "")


def main() -> int:
    if not USER or not PASSWORD:
        print("缺少 SIMNOW_USER_ID / SIMNOW_PASSWORD（.env 未加载？）")
        return 2

    from openctp_ctp.thostmduserapi import (
        CThostFtdcMdApi,
        CThostFtdcMdSpi,
        CThostFtdcReqUserLoginField,
    )

    api = CThostFtdcMdApi.CreateFtdcMdApi("./flow/")

    class Impl(CThostFtdcMdSpi):
        """登录回调（C++ 侧持有引用，impl 保持存活至 Release）。"""

        def __init__(self) -> None:
            super().__init__()
            self.done = False
            self.ok = False

        def OnFrontConnected(self) -> None:
            print("[py-probe] 前置连接成功", flush=True)
            f = CThostFtdcReqUserLoginField()
            f.BrokerID = BROKER
            f.UserID = USER
            f.Password = PASSWORD
            api.ReqUserLogin(f, 1)

        def OnFrontDisconnected(self, reason: int) -> None:
            print(f"[py-probe] 前置断开 reason={reason}", flush=True)

        def OnRspUserLogin(self, pRspUserLogin, pRspInfo, request_id: int, is_last: bool) -> None:
            self.done = True
            if pRspInfo is not None and pRspInfo.ErrorID != 0:
                self.ok = False
                print(f"[py-probe] 登录失败 {pRspInfo.ErrorID}: {pRspInfo.ErrorMsg}", flush=True)
            else:
                self.ok = True
                day = pRspUserLogin.TradingDay if pRspUserLogin else "?"
                print(f"[py-probe] 登录成功 TradingDay={day}", flush=True)

    impl = Impl()
    api.RegisterSpi(impl)
    api.RegisterFront(FRONT)
    print(f"[py-probe] BrokerID={BROKER} UserID={USER} Front={FRONT}", flush=True)
    api.Init()

    for _ in range(250):
        if impl.done:
            break
        time.sleep(0.1)
    api.Release()
    if not impl.done:
        print("[py-probe] 超时：25 秒内未完成登录")
        return 1
    return 0 if impl.ok else 3


if __name__ == "__main__":
    sys.exit(main())
