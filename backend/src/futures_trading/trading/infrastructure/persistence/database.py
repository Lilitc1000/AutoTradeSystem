"""数据库引擎与会话工厂（异步）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ....common.settings import settings
from .base import Base


class Database:
    """异步引擎 + 会话工厂的持有者。"""

    def __init__(self, url: str | None = None) -> None:
        self.url = url or settings.database_url
        self.engine: AsyncEngine = create_async_engine(
            self.url,
            pool_pre_ping=True,  # 断线重连
            pool_size=5,
            max_overflow=5,
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            expire_on_commit=False,  # commit 后属性仍可读（避免隐式 IO）
        )

    async def create_all(self) -> None:
        """直接建表（仅测试用；生产走 alembic 迁移）。"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """事务性会话：异常回滚，正常提交。"""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()


database = Database()
