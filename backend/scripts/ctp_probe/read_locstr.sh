#!/usr/bin/env bash
# 读取 _thostmduserapi.so 中 g_loc 全局对象构造用的 locale 名（0x98dcea）
set -u

SO=/usr/local/lib/python3.13/site-packages/openctp_ctp/_thostmduserapi.so

echo '=== 0x98dce0 附近字节 ==='
objdump -s --start-address=0x98dce0 --stop-address=0x98dd00 "$SO" 2>/dev/null | tail -3

echo '=== python 读取该地址字符串 ==='
python3 - << 'EOF'
from pathlib import Path

so = Path("/usr/local/lib/python3.13/site-packages/openctp_ctp/_thostmduserapi.so")
data = so.read_bytes()

# 通过节头把虚拟地址转文件偏移
import subprocess, re
out = subprocess.run(["readelf", "-S", "-W", str(so)], capture_output=True, text=True).stdout
sections = re.findall(r"\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)", out)
vaddr = 0x98DCEA
for name, addr, off, size in sections:
    addr, off, size = int(addr, 16), int(off, 16), int(size, 16)
    if addr <= vaddr < addr + size:
        file_off = off + (vaddr - addr)
        raw = data[file_off:file_off + 32]
        s = raw.split(b"\x00")[0]
        print(f"节: {name} 文件偏移: {file_off:#x}")
        print(f"字符串: {s!r}")
        break
else:
    print("未找到所在节")
EOF
