#!/usr/bin/env bash
# 运行 C++ td 对照探针（官方 so）：认证 → 登录 → 查询资金账户。
# 用法：docker exec devcontainer-app-1 bash /workspace/backend/scripts/ctp_probe/run_td_probe.sh [FrontAddr]
set -u

FRONT="${1:-tcp://182.254.243.31:30001}"

set -a
. /workspace/.devcontainer/.env
set +a

mkdir -p /tmp/td_probe_flow

# 编译（--no-cache 无需；已编译则跳过）
if [ ! -x /tmp/td_probe ]; then
    g++ -std=c++11 -I/opt/ctp /workspace/backend/scripts/ctp_probe/td_probe.cpp \
        -L/opt/ctp -l:thosttraderapi_se.so -l:thostmduserapi_se.so \
        -Wl,-rpath,/opt/ctp -o /tmp/td_probe || exit 1
fi

timeout 40 /tmp/td_probe "$CTP_BROKER_ID" "$SIMNOW_USER_ID" "$SIMNOW_PASSWORD" "$FRONT" 2>&1 | grep -v SessionDisconnected
