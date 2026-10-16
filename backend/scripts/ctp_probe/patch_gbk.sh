#!/usr/bin/env bash
# 二进制 patch：_thost*.so 中 g_loc 全局对象的 locale 名 "zh_CN.GB18030" → "C"
#
# 根因（gdb + 反汇编实锤）：
#   openctp 的 SWIG 包装含 `static std::locale g_loc("zh_CN.GB18030")`
#   （thostmduserapi.i 用 boost::locale::conv::to_utf(str, "GBK") 需中文 locale）。
#   glibc 容器无 zh_CN.GB18030 locale 定义 → std::locale 构造抛
#   std::runtime_error → .so 静态初始化失败 → import openctp_ctp 即 abort。
#   （glibc 路线试过：locales 包/各种 locale 名/charmap 软链/localedef——
#    glibc 对 locale 名与 LC_IDENTIFICATION codeset 的校验无法满足，放弃。）
#
# 本 patch 为工程止血：locale 名缩为 "C"（等长 \0 填充，std::string 读至 \0）。
#   副作用：boost GBK→UTF8 转换退化为字节透传——CTP 中文字段（品种名等）乱码。
#   技术债跟踪：根治需 openctp 上游修复或在 Dockerfile 源码编译时替换 .i 中的
#   locale 名（见 PROJECT.md「CTP 已知问题」）。
set -eu

SO_MD=$(ls /usr/local/lib/python3.13/site-packages/openctp_ctp/_thostmduserapi.so)
SO_TD=$(ls /usr/local/lib/python3.13/site-packages/openctp_ctp/_thosttraderapi.so)

for SO in "$SO_MD" "$SO_TD"; do
    echo "patch $SO"
    python3 - "$SO" << 'EOF'
import sys
path = sys.argv[1]
data = bytearray(open(path, 'rb').read())
old = 'zh_CN.GB18030'.encode() + b'\x00'   # 14 字节（含结尾 \0）
new = b'C\x00' + b'\x00' * 12               # 等长 14 字节，字符串为 "C"
count = data.count(old)
if count == 0:
    print('  WARN: 未找到目标字符串（可能已 patch 过）')
else:
    data = data.replace(old, new)
    open(path, 'wb').write(data)
    print(f'  replaced {count} occurrence(s)')
EOF
done

echo '=== 验证 import ==='
python3 -c 'from openctp_ctp import thostmduserapi_se, thosttraderapi_se; print("PATCH-IMPORT-OK")' 2>&1 | tail -1
