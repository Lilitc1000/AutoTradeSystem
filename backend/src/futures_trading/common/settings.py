"""应用配置（pydantic-settings）。

优先级：进程环境变量 > .env 文件 > 默认值。
.env 定位：优先仓库根的 .devcontainer/.env（按本文件位置回溯，与 cwd 无关——
进程从任何目录启动都能加载到）；开发环境密钥由 ensure_env.py 生成。
绝不回落到硬编码生产值。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_env_file() -> str:
    """定位 .env：repo 根的 .devcontainer/.env（本文件回溯 4 层），
    回退到 cwd 的 .env（兼容直接运行场景）。"""
    candidate = Path(__file__).resolve().parents[4] / ".devcontainer" / ".env"
    if candidate.exists():
        return str(candidate)
    return ".env"


class Settings(BaseSettings):
    """全局配置。"""

    model_config = SettingsConfigDict(
        env_file=_default_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"

    # 容器内地址（compose 服务名）；compose 会注入完整连接串
    database_url: str = "postgresql+asyncpg://autotrade:autotrade@db:5432/autotrade"
    redis_url: str = "redis://:autotrade@redis:6379"
    redis_key_prefix: str = "autotrade"

    # CTP / SimNow（M2-b 联调；凭证走 .env，M3 迁入 settings 页加密存储）
    # SimNow 2025-06 迁址：第一套(仿真) 30011/30001 跟随交易时段（默认）；
    # 7x24 40011/40001 仅 16:00~次日09:00 服务（深夜非交易时段联调用）
    simnow_user_id: str = ""
    simnow_password: str = ""
    simnow_app_id: str = "simnow_client_test"
    simnow_auth_code: str = "0000000000000000"
    ctp_broker_id: str = "9999"
    ctp_md_front: str = "tcp://182.254.243.31:30011"
    ctp_trade_front: str = "tcp://182.254.243.31:30001"


settings = Settings()
