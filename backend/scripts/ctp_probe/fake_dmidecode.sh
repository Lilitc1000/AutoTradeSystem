#!/bin/sh
# 假 dmidecode（仅 devcontainer 测试环境！）
#
# 背景：CTP td（交易）登录要读硬件机器码（看穿式监管终端识别），实现是
# exec dmidecode。WSL2 内核不透传 DMI 表（宿主同缺）→ 真 dmidecode 无输出
# → 交易登录失败（错误码4 用户不活跃/登录超时，C++ 官方 so 同败，实锤非代码）。
#
# 本脚本返回固定测试 UUID（= 开发机 Windows 宿主机真实 UUID，与 Windows
# 宿主机验证路径保持一致），让容器内 CTP so 能拿到"机器码"完成认证。
#
# ⚠️ 仅限 SimNow 仿真测试环境：SimNow 机器码只做记录不校验真实性。
#    真生产部署必须用真机 + 真 dmidecode（看穿式监管红线，不碰）。
#    安装：/usr/local/bin/dmidecode（PATH 先于 /usr/sbin，覆盖真版）。
#    Dockerfile 由 build ARG CTP_FAKE_DMIDECODE 控制（默认开发镜像装）。

# CTP so 常见调用形式：
#   dmidecode -s system-uuid      → 输出 UUID
#   dmidecode -s system-serial-number → 输出序列号
#   dmidecode（无参，全量）        → 输出最小 DMI 结构（极少走到）
UUID="4C4C4544-0047-5710-8037-C3C04F583334"

case "$1" in
    -s)
        case "$2" in
            system-uuid) echo "$UUID" ;;
            system-serial-number) echo "ATS-DEV-0001" ;;
            system-manufacturer) echo "Dell Inc." ;;
            system-product-name) echo "OptiPlex 7080" ;;
            *) echo "$UUID" ;;
        esac
        ;;
    *)
        # 无参调用：输出最小可用结构（Handle + UUID 行）
        echo "# dmidecode 3.3 (fake, ATS devcontainer)"
        echo "Getting SMBIOS data from sysfs."
        echo "SMBIOS 3.2.0 present."
        echo ""
        echo "Handle 0x0001, DMI type 1, 27 bytes"
        echo "System Information"
        echo "    Manufacturer: Dell Inc."
        echo "    Product Name: OptiPlex 7080"
        echo "    Serial Number: ATS-DEV-0001"
        echo "    UUID: $UUID"
        ;;
esac
exit 0
