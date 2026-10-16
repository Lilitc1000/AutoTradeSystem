#!/usr/bin/env bash
# 在 openctp 源码中查找 SimNow 前置地址配置
set -u

SRC=/tmp/openctp-master

echo '=== openctp 源码中的 SimNow 地址 ==='
grep -rn "180.168.146.187" "$SRC" --include='*.py' --include='*.cpp' --include='*.h' --include='*.json' --include='*.md' --include='*.ini' --include='*.txt' 2>/dev/null | head -10

echo '=== simnow 关键字（不区分大小写）==='
grep -rni "simnow" "$SRC" 2>/dev/null | grep -v Binary | head -10

echo '=== ctpapi-python 的示例/配置文件 ==='
find "$SRC/ctpapi-python" -name '*.json' -o -name '*.ini' -o -name '*.py' -path '*test*' 2>/dev/null | head -8

echo '=== 找含 tcp:// 的配置 ==='
grep -rn "tcp://" "$SRC" --include='*.py' 2>/dev/null | head -10
