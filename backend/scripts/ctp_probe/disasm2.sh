#!/usr/bin/env bash
# 反汇编 _thostmduserapi.so 静态初始化，提取 locale 构造的目标字符串
set -u

SO=/usr/local/lib/python3.13/site-packages/openctp_ctp/_thostmduserapi.so

SYM=$(objdump -t "$SO" 2>/dev/null | grep '__static_initialization_and_destruction_0' | head -1 | awk '{print $1}')
echo "static_init addr: 0x$SYM"
[ -z "$SYM" ] && exit 1

echo '=== 反汇编（call/lea）==='
objdump -d --start-address=0x$SYM --stop-address=$((0x$SYM + 0x200)) "$SO" 2>/dev/null | grep -E 'call|lea' | head -20

echo '=== 反汇编全量（前 60 行）==='
objdump -d --start-address=0x$SYM --stop-address=$((0x$SYM + 0x200)) "$SO" 2>/dev/null | sed -n '7,60p'
