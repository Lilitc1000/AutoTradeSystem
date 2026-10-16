#!/usr/bin/env python3
"""生成 .devcontainer/.env（开发环境基础设施密钥）。

行为（对齐 doc_process_studio scripts/ensure_env.py）：
- 文件不存在：生成全部键
- 文件已存在：只补缺失键，绝不覆盖既有值
- 密钥只落在 .gitignore 忽略的文件里，不进仓库

用法：python3 scripts/ensure_env.py [--env dev]
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

# POSTGRES_USER/DB 有默认值；四个密钥类缺失时自动生成
REQUIRED_KEYS: dict[str, str | None] = {
    "POSTGRES_USER": "autotrade",
    "POSTGRES_DB": "autotrade",
    "POSTGRES_PASSWORD": None,
    "REDIS_PASSWORD": None,
    "JWT_SECRET_KEY": None,
    "SETTINGS_ENCRYPTION_KEY": None,
}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".devcontainer" / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                existing[key.strip()] = value.strip()

    lines: list[str] = []
    for key, default in REQUIRED_KEYS.items():
        if key in existing:
            value = existing[key]
            status = "已有(保留)"
        elif default:
            value = default
            status = "默认值"
        else:
            value = secrets.token_urlsafe(32)
            status = "新生成"
        lines.append(f"{key}={value}")
        print(f"  {key}: {status}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n已写入 {env_path}")
    print("（.gitignore 已忽略该文件，不会入库；删除后重新生成会导致既有数据卷密码错位，请妥善保管）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
