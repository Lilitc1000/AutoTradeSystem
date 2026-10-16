#!/usr/bin/env bash
# 拉取 openctp 源码，将 CTP C++ 头文件与官方预编译 so 固化到 /opt/ctp
# （Dockerfile build 阶段调用；容器重建不丢）
set -eu

mkdir -p /opt/ctp
curl -sL --max-time 180 -o /tmp/openctp.tar.gz \
    https://github.com/openctp/openctp/archive/refs/heads/master.tar.gz
tar xzf /tmp/openctp.tar.gz -C /tmp
SRC=$(ls -d /tmp/openctp-master/ctpapi-python/CTPAPI/linux | head -1)
cp "$SRC"/*.h /opt/ctp/
cp "$SRC"/*.so /opt/ctp/
rm -rf /tmp/openctp.tar.gz /tmp/openctp-master
echo "CTP headers + so 固化到 /opt/ctp:"
ls /opt/ctp/ | tr '\n' ' '
echo
