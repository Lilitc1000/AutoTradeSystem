#!/usr/bin/env bash
# 报单探针批量跑（容器内）：绕开外层 shell 引号地狱（Git Bash + wsl + docker exec
# 三层嵌套引号已坑多次）。用法：docker exec ... bash run_order_test.sh [次数]
# 入口用 python3 -c "gc.disable(); runpy" ——M2-d 实证的 SWIG 稳定入口
# （普通脚本入口在 Linux 上 30-60% 崩，-c 入口 3/3 稳）。
set -u

RUNS="${1:-3}"
CONTRACT="${2:-rb2701}"
PRICE="${3:-2950}"
cd /tmp || exit 1

for i in $(seq 1 "$RUNS"); do
    rm -rf flow_td_probe
    echo "=== run $i/$RUNS ==="
    python3 -c "
import gc, sys
gc.disable()  # SWIG + GC 竞争（M2-d 实锤根因）
sys.argv = ['py_order_probe.py', '$CONTRACT', '$PRICE']
import runpy
runpy.run_path('/workspace/backend/scripts/ctp_probe/py_order_probe.py', run_name='__main__')
" > "/tmp/o$i.log" 2>&1
    rc=$?
    echo "rc=$rc"
    grep -v SessionDisconnected "/tmp/o$i.log" | head -12
    echo "--- 关键步骤数: $(grep -cE '认证成功|登录成功|委托回报|成交回报' "/tmp/o$i.log") ---"
    [ "$rc" -eq 0 ] && break
done
