#!/usr/bin/env bash
# 停止影子链路（strategy_core + md_gateway）。
# 用法：docker exec devcontainer-app-1 bash /workspace/backend/scripts/ctp_probe/stop_chain.sh
set -u

pkill -9 -f 'futures_trading\.processes\.' 2>/dev/null || true
sleep 1
LEFT=$(pgrep -cf 'python3 -u -m futures_trading' || true)
echo "已停止（剩余进程: ${LEFT:-0}）"
