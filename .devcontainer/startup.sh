#!/bin/bash
# AutoTradeSystem 容器入口（PID 1）。
#
# 教训（来自 doc_process_studio）：该脚本作为 PID 1 运行时，
# set -euo pipefail 下任一命令失败都会让整个容器退出，表现为
# "容器打不开/点击没反应"。因此这里刻意不用 set -e：
# 每一步失败只记录日志，绝不影响容器存活。

LOG_DIR=/workspace/.devcontainer/logs
mkdir -p "$LOG_DIR"
echo "[startup] $(date -u +%FT%TZ) container up, workspace=/workspace" >> "$LOG_DIR/startup.log"

# sshd：ats.py 远程操作通道（Windows 侧 SSH 2223 -> 容器 22）
mkdir -p /var/run/sshd
# SFTP 固定用 internal-sftp（sshd 内置，不依赖外部 sftp-server 二进制；
# 2026-10-16 实测外部版在本环境路径解析不稳定，internal-sftp 一次配稳）
sed -i 's|^Subsystem.*sftp.*|Subsystem sftp internal-sftp|' /etc/ssh/sshd_config 2>/dev/null || true
if ! pgrep -x sshd > /dev/null 2>&1; then
    /usr/sbin/sshd >> "$LOG_DIR/sshd.log" 2>&1 \
        || echo "[startup] sshd 启动失败（不影响容器存活，ats.py 将不可用）" >> "$LOG_DIR/startup.log"
fi
echo "[startup] sshd ready on :22 (host 2223)" >> "$LOG_DIR/startup.log"

# editable 注册（M2-c）：镜像已含全部第三方依赖（Dockerfile pip 直装）；
# 这里只做秒级注册（--no-deps，避免 9p 挂载上的慢构建曾卡 10 分钟+）。
# openctp-ctp 由 Dockerfile build 阶段安装+patch（镜像内含，此处不装——
# 避免 pip 重装覆盖 patch 过的 .so）。
if ! python3 -c 'import futures_trading' > /dev/null 2>&1; then
    echo "[startup] editable 注册 futures_trading ..." >> "$LOG_DIR/startup.log"
    (cd /workspace/backend && pip3 install --no-deps -q --break-system-packages -e .) \
        >> "$LOG_DIR/deps.log" 2>&1 \
        || echo "[startup] editable 注册失败（不影响容器存活，跑 verify_env.sh 排查）" >> "$LOG_DIR/startup.log"
fi

# 保持前台（PID 1 不得退出）
exec tail -f /dev/null
