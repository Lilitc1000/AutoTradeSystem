#!/usr/bin/env bash
# import 链诊断：逐个 import 定位 segfault 模块
export PYTHONPATH=/workspace/backend/src
cd /tmp

for m in \
    futures_trading \
    futures_trading.common.settings \
    futures_trading.processes \
    futures_trading.processes.base \
    futures_trading.trading.infrastructure.ctp.adapters \
    futures_trading.trading.infrastructure.messaging.bus \
    futures_trading.processes.md_gateway; do
    if timeout 20 python3 -c "import $m" 2>/tmp/imp_err.log; then
        echo "[$m] OK"
    else
        echo "[$m] FAIL (rc=$?):"
        head -3 /tmp/imp_err.log | sed 's/^/    /'
    fi
done
