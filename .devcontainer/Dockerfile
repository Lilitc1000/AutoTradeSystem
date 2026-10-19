FROM mcr.microsoft.com/devcontainers/python:3.13-bookworm

# 国内 apt 源（阿里云）
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install -y --no-install-recommends \
       curl ca-certificates git build-essential openssh-server \
       postgresql-client redis-tools jq dmidecode lshw \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# dmidecode/lshw：CTP 交易登录采集硬件指纹（看穿式监管终端识别，so 内部
# exec 这两个命令）。WSL2 容器内 DMI 表不透传（/sys/firmware/dmi 不存在），
# 交易登录在 WSL2 devcontainer 无法完成（行情 md 登录不需要故正常）；
# 真机/云部署环境必需，故固化进镜像。
# 诊断记录：backend/scripts/ctp_probe/td_probe.cpp（C++ 对照探针）。
# WSL2 绕行：下方 fake dmidecode（CTP_FAKE_DMIDECODE，默认开发镜像启用）。

# 假 dmidecode（仅测试镜像）：WSL2 无 DMI 表，CTP so exec dmidecode 拿不到
# 机器码 → 认证阶段失败。fake 返回固定测试 UUID（= 开发机 Windows 宿主机
# 真实 UUID，与 Windows 宿主机验证路径一致）。SimNow 机器码只记录不校验，
# 仅限仿真测试；真生产用真机真 dmidecode（看穿式监管红线，不碰）。
# 生产构建：--build-arg CTP_FAKE_DMIDECODE=0 关闭。
ARG CTP_FAKE_DMIDECODE=1
COPY backend/scripts/ctp_probe/fake_dmidecode.sh /tmp/fake_dmidecode.sh
RUN if [ "$CTP_FAKE_DMIDECODE" = "1" ]; then \
        install -m 755 /tmp/fake_dmidecode.sh /usr/local/bin/dmidecode; \
        echo "fake dmidecode installed (test image)"; \
    else \
        echo "fake dmidecode skipped (production build)"; \
    fi \
    && rm /tmp/fake_dmidecode.sh

# sshd + vscode 用户：ats.py 远程操作通道（复刻 doc_process_studio devbox 模式）
RUN mkdir -p /var/run/sshd /home/vscode/.ssh \
    && echo 'vscode:vscode' | chpasswd \
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode \
    && chown -R vscode:vscode /home/vscode

# uv + PyPI 国内镜像
RUN pip3 install --no-cache-dir uv
ENV UV_DEFAULT_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# CTP 支持前置（M2-b 联调用）：
# 1. locales 基建（C++ 扩展的 locale 数据）
# 2. openctp-ctp 安装 + GB18030 patch（见下）
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install -y --no-install-recommends locales \
    && sed -i 's/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen \
    && sed -i 's/# zh_CN.UTF-8/zh_CN.UTF-8/' /etc/locale.gen \
    && locale-gen \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# 依赖安装（pyproject.toml 是唯一真相源，uv + 国内源）：
# - uv 直接读 pyproject.toml 的 dependencies + optional-dependencies +
#   dependency-groups（--extra ctp 装 CTP 绑定，--group dev 装测试/工具链）
# - 改依赖只改 pyproject.toml 一处，rebuild 层缓存只失效本层（改代码不触发）
# - 为什么在镜像装而非容器启动装：/workspace 是 9p 挂载卷，容器内 uv 全量
#   安装曾卡 10 分钟+；镜像内是本地层，uv 快且可复现
# - 版本策略：范围版本（pyproject >= 约束），uv 解析安装；开发环境单源足够，
#   未来生产部署再引入 uv.lock 严格锁定（见 PROJECT.md）
COPY backend/pyproject.toml /tmp/pyproject.toml
RUN cd /tmp \
    && uv pip install --system \
        --index-url https://mirrors.aliyun.com/pypi/simple/ \
        -r pyproject.toml --extra ctp --group dev \
    && rm /tmp/pyproject.toml

# openctp GB18030 patch（依赖装完后执行；版本锁定见 requirements.txt）：
# 根因（gdb + 反汇编定位）：openctp_ctp 的 SWIG 包装含
#   static std::locale g_loc("zh_CN.GB18030")
# glibc 无 zh_CN.GB18030 locale 定义（locales 包只到 GBK，且 LC_IDENTIFICATION
# codeset 校验无法满足）→ std::locale 构造抛 runtime_error → import 即 abort。
# patch：把 .so 中 "zh_CN.GB18030" 缩为 "C"（等长 \0 填充）——C locale 下 boost
# GBK→UTF8 转换退化为字节透传，CTP 中文字段（品种名）乱码（技术债，见 PROJECT.md）。
# 脚本单源真理：backend/scripts/ctp_probe/patch_gbk.sh（容器内热修也用同一脚本）。
# 升级 openctp-ctp 版本时必须重验 patch 是否仍匹配！
COPY backend/scripts/ctp_probe/patch_gbk.sh /tmp/patch_gbk.sh
RUN bash /tmp/patch_gbk.sh \
    && rm /tmp/patch_gbk.sh

# CTP C++ 头文件 + 官方预编译 so 固化到 /opt/ctp（探针/联调用）。
# 不依赖 /tmp（容器重建即丢——M2-c 曾因此导致 C++ 探针编译失败）。
COPY backend/scripts/ctp_probe/fetch_ctp_headers.sh /tmp/fetch_ctp_headers.sh
RUN bash /tmp/fetch_ctp_headers.sh \
    && rm /tmp/fetch_ctp_headers.sh

EXPOSE 22
