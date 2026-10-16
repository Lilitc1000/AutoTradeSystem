"""ORM 模型测试（db 标记：需要真实 PostgreSQL）。

运行条件：设置 ATS_TEST_DATABASE_URL（容器内由 compose 提供 DATABASE_URL）。
未设置时自动跳过（对齐 doc_process_studio 的 db marker 模式）。
db fixture 见 conftest.py。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from futures_trading.trading.infrastructure.persistence.database import Database
from futures_trading.trading.infrastructure.persistence.models import (
    Instrument,
    Order,
    Signal,
    Strategy,
    StrategyVersion,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 16, tzinfo=UTC)


async def test_instrument_roundtrip(db: Database) -> None:
    async with db.session() as session:
        session.add(
            Instrument(
                symbol="rb",
                contract="rb2510",
                name="螺纹钢",
                exchange="SHFE",
                multiplier=Decimal("10"),
                tick_size=Decimal("1"),
                margin_rate=Decimal("0.12"),
                status="MAIN",
                sessions=[{"start": "21:00", "end": "02:30"}],
            )
        )
    async with db.session() as session:
        result = await session.execute(select(Instrument).where(Instrument.symbol == "rb"))
        instrument = result.scalar_one()
        assert instrument.contract == "rb2510"
        assert instrument.multiplier == Decimal("10")
        assert instrument.status == "MAIN"
        assert instrument.sessions == [{"start": "21:00", "end": "02:30"}]


async def test_instrument_unique_constraint(db: Database) -> None:
    async with db.session() as session:
        session.add(
            Instrument(
                symbol="rb",
                contract="rb2510",
                exchange="SHFE",
                multiplier=Decimal("10"),
                tick_size=Decimal("1"),
                margin_rate=Decimal("0.12"),
            )
        )
    with pytest.raises(IntegrityError):
        async with db.session() as session:
            session.add(
                Instrument(
                    symbol="rb",
                    contract="rb2510",
                    exchange="SHFE",
                    multiplier=Decimal("10"),
                    tick_size=Decimal("1"),
                    margin_rate=Decimal("0.12"),
                )
            )


async def test_strategy_version_chain(db: Database) -> None:
    async with db.session() as session:
        strategy = Strategy(strategy_id="trend_atr_v1", type="TREND_ATR", universe=["rb", "hc"])
        strategy.versions.append(StrategyVersion(version=1, params={"fast": 5, "slow": 20}))
        strategy.versions.append(StrategyVersion(version=2, params={"fast": 8, "slow": 34}))
        session.add(strategy)
    async with db.session() as session:
        # relationship 必须 eager load：async session 下 lazy load 会抛 MissingGreenlet；
        # 集合用 selectinload（joinedload 一对多会要求 .unique()）
        result = await session.execute(
            select(Strategy)
            .options(selectinload(Strategy.versions))
            .where(Strategy.strategy_id == "trend_atr_v1")
        )
        loaded = result.scalar_one()
        assert [v.version for v in loaded.versions] == [1, 2]
        assert loaded.versions[1].params == {"fast": 8, "slow": 34}


async def test_order_defaults_and_constraint(db: Database) -> None:
    async with db.session() as session:
        session.add(
            Order(
                order_id="BT-000001",
                idempotency_key="key-1",
                instance_id="inst-1",
                contract="rb2510",
                symbol="rb",
                direction="LONG",
                offset="OPEN",
                quantity=Decimal("2"),
                price_type="LIMIT",
                limit_price=Decimal("3500"),
            )
        )
    async with db.session() as session:
        result = await session.execute(select(Order).where(Order.order_id == "BT-000001"))
        order = result.scalar_one()
        assert order.status == "PENDING"
        assert order.filled_quantity == Decimal("0")

    # filled_quantity <= quantity 约束
    with pytest.raises(IntegrityError):
        async with db.session() as session:
            session.add(
                Order(
                    order_id="BT-000002",
                    idempotency_key="key-2",
                    instance_id="inst-1",
                    contract="rb2510",
                    symbol="rb",
                    direction="LONG",
                    offset="OPEN",
                    quantity=Decimal("1"),
                    price_type="LIMIT",
                    limit_price=Decimal("3500"),
                    filled_quantity=Decimal("5"),
                )
            )


async def test_signal_append(db: Database) -> None:
    async with db.session() as session:
        session.add_all(
            [
                Signal(
                    signal_id="sig-1",
                    instance_id="inst-1",
                    symbol="rb",
                    direction="LONG",
                    reason="SMA 上穿",
                    ts=NOW,
                ),
                Signal(
                    signal_id="sig-2",
                    instance_id="inst-1",
                    symbol="rb",
                    direction="LONG",
                    reason="SMA 上穿",
                    rejected_reason="R07_DAILY_LOSS_LIMIT",
                    ts=NOW,
                ),
            ]
        )
    async with db.session() as session:
        result = await session.execute(select(Signal).where(Signal.instance_id == "inst-1"))
        signals = result.scalars().all()
        assert len(signals) == 2
        assert any(s.rejected_reason == "R07_DAILY_LOSS_LIMIT" for s in signals)
