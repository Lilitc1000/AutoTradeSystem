FROM mcr.microsoft.com/devcontainers/python:3.13-bookworm

# 国内 apt 源（阿里云）
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install -y --no-install-recommends \
       curl ca-certificates git build-essential openssh-server \
       postgresql-client redis-tools jq \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

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

# openctp CTP 绑定 + 关键 patch（版本锁定 6.7.11.0）：
# 根因（gdb + 反汇编定位）：openctp_ctp 的 SWIG 包装含
#   static std::locale g_loc("zh_CN.GB18030")
# glibc 无 zh_CN.GB18030 locale 定义（locales 包只到 GBK，且 LC_IDENTIFICATION
# codeset 校验无法满足）→ std::locale 构造抛 runtime_error → import 即 abort。
# patch：把 .so 中 "zh_CN.GB18030" 缩为 "C"（等长 \0 填充）——C locale 下 boost
# GBK→UTF8 转换退化为字节透传，CTP 中文字段（品种名）乱码（技术债，见 PROJECT.md）。
# 脚本单源真理：backend/scripts/ctp_probe/patch_gbk.sh（容器内热修也用同一脚本）。
# 升级 openctp-ctp 版本时必须重验 patch 是否仍匹配！
COPY backend/scripts/ctp_probe/patch_gbk.sh /tmp/patch_gbk.sh
RUN pip3 install --no-cache-dir "openctp-ctp==6.7.11.0" \
    && bash /tmp/patch_gbk.sh \
    && rm /tmp/patch_gbk.sh

# 项目依赖进镜像（M2-c：editable 注册放 startup.sh——/workspace 是 9p
# 挂载卷，uv/pip 在其上构建 editable 元数据极慢，曾卡 10 分钟+）。
# 装非项目依赖本体；版本与 backend/pyproject.toml 对齐，改依赖时同步更新。
RUN pip3 install --no-cache-dir \
    "sqlalchemy[asyncio]>=2.0" "asyncpg>=0.29" "alembic>=1.13" \
    "pydantic-settings>=2.3" "fastapi>=0.115" "uvicorn[standard]>=0.30" \
    "redis>=5.2" "pandas>=2.2" "pyarrow>=15.0" \
    "pytest>=8.3.0" "pytest-cov>=5.0.0" "pytest-asyncio>=0.24" \
    "ruff>=0.6.0" "mypy>=1.11.0" "akshare>=1.14"

EXPOSE 22
