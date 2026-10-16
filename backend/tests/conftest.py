"""测试公共 fixtures。"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from futures_trading.trading.domain.model.instrument import Exchange, Instrument
from futures_trading.trading.infrastructure.persistence.database import Database

START = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


@pytest.fixture
def rb_instrument() -> Instrument:
    """螺纹钢主力合约（乘数 10，最小变动 1 元，保证金 12%）。"""
    return Instrument(
        symbol="rb",
        contract="rb2510",
        name="螺纹钢",
        exchange=Exchange.SHFE,
        multiplier=Decimal("10"),
        tick_size=Decimal("1"),
        margin_rate=Decimal("0.12"),
    )


@pytest.fixture
def start_ts() -> datetime:
    return START


@pytest_asyncio.fixture
async def db() -> Database:
    """真实 PostgreSQL（ATS_TEST_DATABASE_URL 或 DATABASE_URL 设置时可用）。

    未设置时自动跳过（对齐 doc_process_studio 的 db marker 模式）。
    """
    url = os.getenv("ATS_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("未设置 ATS_TEST_DATABASE_URL / DATABASE_URL，跳过真实 PG 测试")
    database = Database(url)
    await database.create_all()
    yield database
    await database.drop_all()
    await database.dispose()
