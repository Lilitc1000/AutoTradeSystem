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

EXPOSE 22
