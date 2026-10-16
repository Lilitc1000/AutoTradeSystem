"""应用配置（pydantic-settings）。

优先级：进程环境变量 > .env 文件 > 默认值。
开发环境由 .devcontainer/.env + compose environment 注入；绝不回落到硬编码生产值。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"

    # 容器内地址（compose 服务名）；compose 会注入完整连接串
    database_url: str = "postgresql+asyncpg://autotrade:autotrade@db:5432/autotrade"
    redis_url: str = "redis://:autotrade@redis:6379"
    redis_key_prefix: str = "autotrade"


settings = Settings()
