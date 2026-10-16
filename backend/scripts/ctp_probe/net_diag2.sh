#!/usr/bin/env bash
# 测试 SimNow 新前置地址（2025-06 迁址公告）
set -u

echo '=== 新前置连通性 ==='
for hp in 182.254.243.31:30011 182.254.243.31:30001 182.254.243.31:40011 182.254.243.31:40001; do
    host=${hp%:*}
    port=${hp#*:}
    if timeout 8 bash -c "cat < /dev/null > /dev/tcp/$host/$port" 2>/dev/null; then
        echo "$hp OPEN"
    else
        echo "$hp CLOSED/超时"
    fi
done
