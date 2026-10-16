#!/usr/bin/env bash
# 诊断容器到 SimNow 前置的网络连通性
set -u

echo '=== DNS 解析 ==='
getent hosts 180.168.146.187 || echo 'DNS 解析失败（IP 直连跳过 DNS）'

echo '=== TCP 连通性测试（各前置）==='
for hp in 180.168.146.187:10131 180.168.146.187:10130 180.168.146.187:10110 180.168.146.187:10111; do
    host=${hp%:*}
    port=${hp#*:}
    if timeout 5 bash -c "cat < /dev/null > /dev/tcp/$host/$port" 2>/dev/null; then
        echo "$hp OPEN"
    else
        echo "$hp CLOSED/超时"
    fi
done

echo '=== 出网对照（已知通的）==='
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/mirrors.aliyun.com/443' 2>/dev/null && echo 'aliyun:443 OPEN' || echo 'aliyun:443 FAIL'
