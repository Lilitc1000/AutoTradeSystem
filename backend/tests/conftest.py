"""测试公共 fixtures。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from futures_trading.trading.domain.model.instrument import Exchange, Instrument

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
