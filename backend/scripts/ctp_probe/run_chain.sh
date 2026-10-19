#!/usr/bin/env bash
# 启动完整影子链路（CTP 实时行情源 → 总线 → strategy_core 影子决策）。
# 用法：docker exec devcontainer-app-1 bash /workspace/backend/scripts/ctp_probe/run_chain.sh [合约] [周期]
#  合约默认 rb2701（主力）；周期默认 min1
# 日志：/tmp/md_gw.log（行情网关，守护循环）、/tmp/sc.log（策略引擎）
#
# 注意：openctp 在 asyncio 事件循环下偶发 segfault（~60%，C++ 线程与事件循环
# 竞争，已知技术债）——md_gateway 用守护循环自动重启；根治方案（CTP api 跑
# 独立线程）见 PROJECT.md 技术债。
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

# md_gateway 守护循环（崩了 3 秒内自动重启，重启记入日志）
setsid bash -c '
    TAG="md_gateway_guard"
    i=0
    while true; do
        i=$((i+1))
        echo "[guard] 第 $i 次启动 md_gateway $(date +%T)" >> /tmp/md_gw.log
        python3 -u -m futures_trading.processes.md_gateway --source ctp \
            --symbols '"$CONTRACT"' --interval '"$INTERVAL"' \
            < /dev/null >> /tmp/md_gw.log 2>&1
        echo "[guard] md_gateway 退出（rc=$?），3 秒后重启" >> /tmp/md_gw.log
        sleep 3
    done
' < /dev/null > /dev/null 2>&1 &
sleep 5

echo "链路已启动: 合约=$CONTRACT 周期=$INTERVAL（md_gateway 守护中）"
pgrep -af 'python3 -u -m futures_trading' | sed 's/^/  /'
