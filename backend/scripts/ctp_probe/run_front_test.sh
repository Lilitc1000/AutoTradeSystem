#!/usr/bin/env bash
# 指定交易前置跑报单探针（绕开内联引号地狱）。用法：bash run_front_test.sh [front]
set -u
FRONT="${1:-tcp://182.254.243.31:30001}"
CONTRACT="${2:-rb2701}"
PRICE="${3:-2950}"
cd /tmp || exit 1
rm -rf flow_td_probe
echo "=== front=$FRONT contract=$CONTRACT price=$PRICE ==="
ORDER_PROBE_FRONT="$FRONT" python3 -c "
import gc, sys
gc.disable()
sys.argv = ['py_order_probe.py', '$CONTRACT', '$PRICE']
import runpy
runpy.run_path('/workspace/backend/scripts/ctp_probe/py_order_probe.py', run_name='__main__')
" > /tmp/front.log 2>&1
echo "rc=$?"
grep -v SessionDisconnected /tmp/front.log | head -12
