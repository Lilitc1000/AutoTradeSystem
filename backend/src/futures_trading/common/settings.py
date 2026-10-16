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

    # CTP / SimNow（M2-b 联调；凭证走 .env，M3 迁入 settings 页加密存储）
    # SimNow 2025-06 迁址：第一套(仿真) 30011/30001 跟随交易时段；
    # 7x24 40011/40001 服务时间 16:00~次日09:00（新用户第三交易日后可用第二套）
    simnow_user_id: str = ""
    simnow_password: str = ""
    simnow_app_id: str = "simnow_client_test"
    simnow_auth_code: str = "0000000000000000"
    ctp_broker_id: str = "9999"
    ctp_md_front: str = "tcp://182.254.243.31:40011"
    ctp_trade_front: str = "tcp://182.254.243.31:40001"


settings = Settings()
