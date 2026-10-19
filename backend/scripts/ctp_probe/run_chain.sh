#!/usr/bin/env bash
# 启动完整影子链路（CTP 实时行情源 → 总线 → strategy_core 影子决策）。
# 用法：docker exec devcontainer-app-1 bash /workspace/backend/scripts/ctp_probe/run_chain.sh [合约] [周期]
#  合约默认 rb2701（主力）；周期默认 min1
# 日志：/tmp/md_gw.log（行情网关，守护循环）、/tmp/sc.log（策略引擎）
#
# 入口说明：md_gateway 用 `python3 -c "gc.disable(); runpy.run_module(...)"`
# 而非 `python3 -m`——实证（2026-10-19）：-m 入口下 openctp 的 SWIG
# CreateFtdcMdApi 偶发 segfault（GC 交互，禁 GC 后仍有 ~40%），-c 入口
# 3/3 稳定（启动路径/内存布局差异）。根因与根治见 PROJECT.md 技术债。
set -u

CONTRACT="${1:-rb2701}"
INTERVAL="${2:-min1}"

pkill -9 -f 'futures_trading\.processes\.' 2>/dev/null || true
pkill -9 -f 'md_gateway_guard' 2>/dev/null || true
sleep 1

rm -rf /tmp/futures_ctp
mkdir -p /tmp/futures_ctp

cd /tmp
export PYTHONPATH=/workspace/backend/src

# strategy_core（稳定，无需守护）
setsid python3 -u -m futures_trading.processes.strategy_core \
    --symbols rb --params '{}' \
    < /dev/null > /tmp/sc.log 2>&1 &
sleep 2

# md_gateway：守护循环 + -c 入口（gc.disable + runpy）
setsid bash -c '
    while true; do
        echo "[guard] 启动 md_gateway $(date +%T)" >> /tmp/md_gw.log
        python3 -u -c "
import gc, sys, runpy
gc.disable()
sys.argv = [\"md_gateway\", \"--source\", \"ctp\", \"--symbols\", \"'"$CONTRACT"'\", \"--interval\", \"'"$INTERVAL"'\"]
runpy.run_module(\"futures_trading.processes.md_gateway\", run_name=\"__main__\")
" < /dev/null >> /tmp/md_gw.log 2>&1
        echo "[guard] md_gateway 退出（rc=$?），3 秒后重启" >> /tmp/md_gw.log
        sleep 3
    done
' < /dev/null > /dev/null 2>&1 &
sleep 5

echo "链路已启动: 合约=$CONTRACT 周期=$INTERVAL（md_gateway -c 入口 + 守护）"
pgrep -af 'futures_trading.processes' | sed 's/^/  /'
