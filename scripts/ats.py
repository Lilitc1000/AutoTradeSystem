#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ats.py —— 访问 AutoTradeSystem 开发容器 (WSL2/Docker devcontainer) 的统一入口

复刻 doc_process_studio 的 devbox 模式：SSH 进容器操作，容器 /workspace
挂载 WSL 项目目录，容器内修改直接落在 WSL 文件系统上。

连接信息:
  主机     127.0.0.1:2223   （与 doc_process_studio 的 2222 错开，可并行）
  用户     vscode
  认证     ~/.ssh/devcontainer_ed25519 (免密) / 失败回退口令 vscode
  远端根目录 /workspace
  本地镜像   C:\\Users\\autotoll\\.workbuddy\\ats-mirror

用法:
  python ats.py exec "shell 命令"       远程执行命令 (工作目录 /workspace)
  python ats.py ls  <远端路径>           列出远端目录
  python ats.py cat <远端文件>           读取远端文件
  python ats.py put <本地文件> <远端路径> 上传本地文件到远端（覆盖式，SFTP）
  python ats.py sync [--dry-run]        增量镜像源码到本地
  python ats.py info                    打印连接与镜像状态
"""

import os
import sys
import stat
import time
import fnmatch

try:
    import paramiko
except ImportError:
    sys.exit("缺少 paramiko，请先安装: pip install paramiko")

HOST = "127.0.0.1"
PORT = 2223
USER = "vscode"
KEY_PATH = os.path.expanduser("~/.ssh/devcontainer_ed25519")
REMOTE_ROOT = "/workspace"

# 免密失效时的兜底口令（容器重建后 authorized_keys 会丢）
PASSWORD = os.environ.get("ATS_PASSWORD", "vscode")

LOCAL_MIRROR = r"C:\Users\autotoll\.workbuddy\ats-mirror"
STATE_FILE = os.path.join(LOCAL_MIRROR, ".sync_state.tsv")

EXCLUDE_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".husky", ".kilo",
    "logs", "log", ".idea", ".vscode-server", ".cache", "data",
}
EXCLUDE_PATTERNS = ("*.log", "*.pyc", "*.pyo", "*.tsbuildinfo")
MAX_FILE_SIZE = 4 * 1024 * 1024


def connect():
    """先试免密；容器重建导致 authorized_keys 丢失时自动回退到口令认证。"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    last_err = None
    if os.path.exists(KEY_PATH):
        try:
            client.connect(HOST, port=PORT, username=USER,
                           pkey=paramiko.Ed25519Key.from_private_key_file(KEY_PATH),
                           timeout=15)
            return client
        except Exception as e:      # noqa: BLE001
            last_err = e
            client.close()
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=15)
        return client
    except Exception as e:          # noqa: BLE001
        raise SystemExit(
            f"SSH 连接失败（免密与口令均不可用）\n"
            f"  免密: {last_err}\n  口令: {e}\n"
            f"  排查: 容器是否运行? wsl -d Ubuntu -- docker ps | grep autotrade ; 端口 2223 是否映射?"
        )


def _connect_sftp():
    client = connect()
    return client, client.open_sftp()


def _excluded(name, is_dir):
    if is_dir:
        return name in EXCLUDE_DIRS or name.startswith(".")
    return any(fnmatch.fnmatch(name, p) for p in EXCLUDE_PATTERNS)


def _norm_remote(path: str) -> str:
    """规范化远端路径。

    两个用途：
    1. Git Bash 的 MSYS 路径转换会把 /workspace 改写成 Windows 路径——
       调用方可用 //workspace（双斜杠）规避，这里负责折叠多余斜杠；
    2. 更稳妥的做法是调用前设置 MSYS_NO_PATHCONV=1（见 PROJECT.md）。
    """
    while path.startswith("//"):
        path = path[1:]
    return path


# ---------------------------------------------------------------- commands
def cmd_exec(cmd, cwd=REMOTE_ROOT):
    client = connect()
    try:
        full = f"cd {cwd} && {cmd}" if cwd else cmd
        _, out, err = client.exec_command(full, timeout=300)
        code = out.channel.recv_exit_status()
        so = out.read().decode("utf-8", "replace")
        se = err.read().decode("utf-8", "replace")
        if so:
            print(so, end="" if so.endswith("\n") else "\n")
        if se:
            print(se, end="" if se.endswith("\n") else "\n", file=sys.stderr)
        return code
    finally:
        client.close()


def cmd_ls(path):
    path = _norm_remote(path)
    client, sftp = _connect_sftp()
    try:
        for e in sorted(sftp.listdir_attr(path), key=lambda x: x.filename):
            mode = e.longname.split()[0] if e.longname else "?"
            kind = "d" if stat.S_ISDIR(e.st_mode) else "-"
            size = "" if kind == "d" else f"{e.st_size:>10}"
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.st_mtime))
            print(f"{kind}{mode[1:]:<9} {size:>10}  {mtime}  {e.filename}")
    finally:
        sftp.close()
        client.close()


def cmd_cat(path):
    path = _norm_remote(path)
    client, sftp = _connect_sftp()
    try:
        with sftp.open(path, "r") as f:
            sys.stdout.write(f.read().decode("utf-8", "replace"))
    finally:
        sftp.close()
        client.close()


def cmd_put(local_path, remote_path):
    """上传本地文件到远端（SFTP 覆盖写，绕开 UNC 文件系统限制）。"""
    if not os.path.isfile(local_path):
        sys.exit(f"本地文件不存在: {local_path}")
    remote_path = _norm_remote(remote_path)
    if not remote_path.startswith("/"):
        remote_path = f"{REMOTE_ROOT}/{remote_path}"
    client, sftp = _connect_sftp()
    try:
        remote_dir = os.path.dirname(remote_path)
        try:
            sftp.stat(remote_dir)
        except IOError:
            # 远端目录不存在则逐级创建
            parts = remote_dir.lstrip("/").split("/")
            cur = ""
            for part in parts:
                cur += f"/{part}"
                try:
                    sftp.stat(cur)
                except IOError:
                    sftp.mkdir(cur)
        size = os.path.getsize(local_path)
        sftp.put(local_path, remote_path)
        print(f"已上传 {local_path} -> {remote_path} ({size} bytes)")
    finally:
        sftp.close()
        client.close()


def cmd_sync(dry_run=False):
    client, sftp = _connect_sftp()
    os.makedirs(LOCAL_MIRROR, exist_ok=True)
    prev = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 3:
                    prev[parts[0]] = (int(parts[1]), int(parts[2]))

    seen, added, updated, skipped = {}, 0, 0, 0
    stack = [("", REMOTE_ROOT)]
    while stack:
        rel, rpath = stack.pop()
        try:
            entries = list(sftp.listdir_attr(rpath))
        except (IOError, OSError):
            continue
        for e in entries:
            is_dir = stat.S_ISDIR(e.st_mode)
            if _excluded(e.filename, is_dir):
                continue
            rrel = f"{rel}/{e.filename}" if rel else e.filename
            if is_dir:
                stack.append((rrel, f"{rpath}/{e.filename}"))
                continue
            if e.st_size > MAX_FILE_SIZE:
                skipped += 1
                continue
            seen[rrel] = (int(e.st_mtime), int(e.st_size))
            local = os.path.join(LOCAL_MIRROR, rrel.replace("/", os.sep))
            if rrel in prev and prev[rrel] == seen[rrel] and os.path.exists(local):
                continue
            action = "新增" if rrel not in prev else "更新"
            if dry_run:
                print(f"[{action}] {rrel}")
                continue
            os.makedirs(os.path.dirname(local), exist_ok=True)
            try:
                sftp.get(f"{rpath}/{e.filename}", local)
                os.utime(local, (e.st_mtime, e.st_mtime))
                added += 1 if action == "新增" else 0
                updated += 0 if action == "新增" else 1
            except (IOError, OSError) as ex:
                print(f"[跳过] {rrel}: {ex}")

    removed = 0
    if not dry_run:
        for rrel in set(prev) - set(seen):
            local = os.path.join(LOCAL_MIRROR, rrel.replace("/", os.sep))
            if os.path.exists(local):
                os.remove(local)
                removed += 1

    sftp.close()
    client.close()

    if not dry_run:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            for k, (m, s) in sorted(seen.items()):
                f.write(f"{k}\t{m}\t{s}\n")

    print(
        f"同步完成 -> {LOCAL_MIRROR}\n"
        f"  新增 {added} / 更新 {updated} / 删除 {removed} / 跳过(超大) {skipped} / 合计 {len(seen)} 个文件"
    )


def cmd_info():
    print(f"远端   : {USER}@{HOST}:{PORT}:{REMOTE_ROOT}")
    print(f"密钥   : {KEY_PATH}  (存在={os.path.exists(KEY_PATH)})")
    print(f"镜像   : {LOCAL_MIRROR}")
    if os.path.exists(STATE_FILE):
        n = sum(1 for _ in open(STATE_FILE, encoding="utf-8"))
        mt = time.strftime("%Y-%m-%d %H:%M:%S",
                           time.localtime(os.path.getmtime(STATE_FILE)))
        print(f"上次同步: {mt}  ({n} 个文件)")
    else:
        print("上次同步: 尚未同步")


# ---------------------------------------------------------------- main
def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    c, *rest = sys.argv[1:]
    if c == "exec":
        sys.exit(cmd_exec(" ".join(rest)))
    if c == "ls":
        cmd_ls(rest[0] if rest else REMOTE_ROOT)
    elif c == "cat":
        cmd_cat(rest[0])
    elif c == "put":
        if len(rest) < 2:
            sys.exit("用法: ats.py put <本地文件> <远端路径>")
        cmd_put(rest[0], rest[1])
    elif c == "sync":
        cmd_sync(dry_run="--dry-run" in rest)
    elif c == "info":
        cmd_info()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
