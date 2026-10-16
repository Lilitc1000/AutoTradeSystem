#!/usr/bin/env bash
# 构建并运行 CTP 登录探针（容器内执行）
# 用法: docker exec devcontainer-app-1 bash /workspace/backend/scripts/ctp_probe/run.sh [前端地址]
set -eu

SRC=/tmp/openctp-master/ctpapi-python/CTPAPI/linux
PROBE=/workspace/backend/scripts/ctp_probe

echo '=== 编译 md_login ==='
# 官方 so 无 lib 前缀，直接传 .so 路径
g++ -std=c++11 -O1 -I"$SRC" "$PROBE/md_login.cpp" \
    "$SRC/thostmduserapi_se.so" "$SRC/thosttraderapi_se.so" \
    -Wl,-rpath,"$SRC" -o /tmp/md_login
echo '编译成功'

echo '=== 读取凭证（从 .env，不回显密码）==='
set -a
. /workspace/.devcontainer/.env
set +a

FRONT="${1:-${CTP_MD_FRONT:-tcp://180.168.146.187:10131}}"
echo "BrokerID=${CTP_BROKER_ID:-9999} UserID=${SIMNOW_USER_ID} Front=${FRONT}"

echo '=== 运行探针（25 秒超时）==='
mkdir -p /tmp/flow
cd /tmp
/tmp/md_login "${CTP_BROKER_ID:-9999}" "${SIMNOW_USER_ID}" "${SIMNOW_PASSWORD}" "$FRONT"
