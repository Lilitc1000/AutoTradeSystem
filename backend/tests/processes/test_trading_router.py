"""交易域 API 测试（M3-a）：REST 查询 + SSE 流。

路由即 async 函数——直接调用验证 payload（不绕 HTTP 栈）；
真 PG 由 db fixture 提供（未设置 ATS_TEST_DATABASE_URL 时自动跳过）。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from futures_trading.trading.infrastructure.persistence import models
from futures_trading.trading.infrastructure.persistence.database import Database
from futures_trading.trading.router import stream as stream_module
from futures_trading.trading.router import trading as trading_module

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def trading_db():
    """建表 + 造交易域数据；无 PG 时跳过。"""
    url = os.getenv("ATS_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("未设置 ATS_TEST_DATABASE_URL / DATABASE_URL")
    from sqlalchemy import text

    from futures_trading.trading.infrastructure.persistence.base import Base

    db = Database(url)
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db.session() as session:
        session.add(
            models.Position(
                instance_id="i1",
                contract="rb2701",
                direction="LONG",
                quantity=Decimal("3"),
                avg_price=Decimal("3050"),
            )
        )
        session.add(
            models.Order(
                order_id="o-1",
                idempotency_key="k-1",
                instance_id="i1",
                contract="rb2701",
                symbol="rb",
                direction="LONG",
                offset="OPEN",
                quantity=Decimal("1"),
                price_type="LIMIT",
                limit_price=Decimal("3070"),
                status="FILLED",
                filled_quantity=Decimal("1"),
                avg_fill_price=Decimal("3067"),
            )
        )
        session.add(
            models.DailyPnl(
                instance_id="i1",
                date=datetime(2026, 10, 20, tzinfo=UTC),
                realized=Decimal("1200"),
                unrealized=Decimal("-300"),
                equity=Decimal("101900"),
            )
        )
        await session.commit()
    yield db
    async with db.engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'DELETE FROM "{table.name}"'))
    await db.dispose()


class TestTradingRouter:
    async def test_list_positions(self, trading_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(trading_module, "database", trading_db)
        result = await trading_module.list_positions()
        assert len(result["positions"]) == 1
        pos = result["positions"][0]
        assert pos["contract"] == "rb2701"
        assert pos["quantity"] == "3"  # Decimal → str 保精度

    async def test_list_orders_filter_by_status(self, trading_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(trading_module, "database", trading_db)
        result = await trading_module.list_orders(status="filled", instance_id=None, limit=100)
        assert len(result["orders"]) == 1
        assert result["orders"][0]["status"] == "FILLED"

    async def test_account_summary(self, trading_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(trading_module, "database", trading_db)
        result = await trading_module.account_summary()
        assert result["equity"] == "101900"
        assert result["realized"] == "1200"
        assert result["pnl_date"] is not None


class TestStreamRouter:
    async def test_rejects_unknown_channel(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException, match="不允许订阅"):
            await stream_module.stream(channels="internal.secret")

    async def test_rejects_empty_channels(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException, match="不能为空"):
            await stream_module.stream(channels="  ,  ")

    async def test_forwards_redis_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SSE 生成器把 Redis 消息转为 data: JSON 行。"""

        class _FakePubsub:
            def __init__(self) -> None:
                self._sent = False

            async def subscribe(self, *_channels: str) -> None:
                return None

            async def get_message(self, ignore_subscribe_messages: bool = False, timeout: float | None = None):
                import asyncio

                if not self._sent:
                    self._sent = True
                    return {"type": "message", "channel": "signal.outcome", "data": '{"status": "EXECUTED"}'}
                await asyncio.sleep(0.01)
                return None

            async def unsubscribe(self) -> None:
                return None

        class _FakeRedis:
            def pubsub(self) -> _FakePubsub:
                return _FakePubsub()

            async def close(self) -> None:
                return None

        monkeypatch.setattr(stream_module.redis, "from_url", lambda *_a, **_k: _FakeRedis())
        response = await stream_module.stream(channels="signal.outcome,trade.fill")
        line = await response.body_iterator.__anext__()
        payload = json.loads(line.removeprefix("data: ").strip())
        assert payload["channel"] == "signal.outcome"
        assert payload["payload"] == {"status": "EXECUTED"}
