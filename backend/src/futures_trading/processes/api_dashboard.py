"""api_dashboard：监控面板 API 进程。

- /healthz：db + redis + 三 worker 心跳
- /api/backtest/*：回测任务 / 报告 / 参数扫描（M1）
只读观测 + 显式干预指令，永不直接下单（设计文档 2.2）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI
from sqlalchemy import text

from ..common.settings import settings
from ..trading.infrastructure.persistence.database import database
from ..trading.router.accounts import router as accounts_router
from ..trading.router.backtest import router as backtest_router
from ..trading.router.stream import router as stream_router
from ..trading.router.trading import router as trading_router

# 被监控的 worker 进程（写 Redis 心跳）。api_dashboard 自身不在列——
# 能访问到 healthz 即证明它活着。
WORKER_PROCESSES = ("md_gateway", "strategy_core", "trade_gateway")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await database.dispose()


app = FastAPI(title="AutoTradeSystem Dashboard API", version="0.1.0", lifespan=lifespan)
app.include_router(backtest_router)
app.include_router(accounts_router)
app.include_router(trading_router)
app.include_router(stream_router)


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    """健康检查：db / redis / 四进程心跳。"""
    db_ok = False
    try:
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001  # 健康检查不抛异常，降级返回
        pass

    redis_ok = False
    heartbeats: dict[str, bool] = {}
    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        redis_ok = await client.ping()
        for name in WORKER_PROCESSES:
            heartbeats[name] = bool(await client.exists(f"{settings.redis_key_prefix}:proc:{name}:hb"))
        await client.aclose()
    except Exception:  # noqa: BLE001
        pass

    ok = db_ok and redis_ok and all(heartbeats.values())
    return {"status": "ok" if ok else "degraded", "db": db_ok, "redis": redis_ok, "processes": heartbeats}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)


if __name__ == "__main__":
    main()
