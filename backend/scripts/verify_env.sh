#!/usr/bin/env bash
# 环境重建后一键验证（M2-c 固化配套）。
# 用法：docker exec devcontainer-app-1 bash /workspace/backend/scripts/verify_env.sh
# 覆盖：CTP 绑定 import / alembic 迁移 / pytest / SimNow 登录 / 回放影子链路
set -u

cd /workspace/backend
FAIL=0

step() { echo; echo "=== $1 ==="; }

step '1/5 openctp_ctp import（patch 生效验证）'
# 注意：md+td 同时 import 的进程退出时可能 segfault（openctp atexit 析构问题，
# 已知技术债见 PROJECT.md）——以是否打印 OK 判定，不看待进程退出码
python3 -c 'from openctp_ctp.thostmduserapi import CThostFtdcMdApi; from openctp_ctp.thosttraderapi import CThostFtdcTraderApi; print("OK")' 2>&1 | grep -q '^OK$' && echo 'OK（退出时 segfault 为已知问题，不影响使用）' || FAIL=1

step '2/5 alembic 迁移状态'
alembic current 2>/dev/null | tail -1 || FAIL=1

step '3/5 pytest 全量'
PYTHONPATH=src python3 -m pytest tests -q 2>&1 | tail -1 || FAIL=1

step '4/5 SimNow 登录（Python 绑定真实连接）'
set -a
. /workspace/.devcontainer/.env 2>/dev/null || true
set +a
rm -rf /tmp/flow && mkdir -p /tmp/flow
(cd /tmp && PYTHONPATH=/workspace/backend/src python3 /workspace/backend/scripts/ctp_probe/py_login.py 2>&1 | tail -2) || FAIL=1

step '5/5 回放影子链路（md_gateway → 总线 → strategy_core）'
PYTHONPATH=src setsid python3 -m futures_trading.processes.strategy_core \
    --symbols rb --params '{"fast_window":5,"slow_window":30}' \
    < /dev/null > /tmp/verify_sc.log 2>&1 &
SC_PID=$!
sleep 3
PYTHONPATH=src python3 -m futures_trading.processes.md_gateway \
    --source replay --symbols rb --interval day --mode fast --start 2026-06-01 2>&1 | tail -1
sleep 3
kill $SC_PID 2>/dev/null
grep -q 'EXECUTED\|IGNORED\|REJECTED' /tmp/verify_sc.log && echo '影子链路 OK（见 /tmp/verify_sc.log）' || { echo '影子链路无信号输出'; FAIL=1; }

echo
if [ "$FAIL" = "0" ]; then
    echo '########## verify_env 全部通过 ##########'
else
    echo '########## verify_env 存在失败项 ##########'
fi
exit $FAIL
