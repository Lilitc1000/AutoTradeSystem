"""BarAggregator 测试：tick 流 → bar 聚合。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from futures_trading.common.types import Price, Quantity
from futures_trading.trading.application.dtos import Tick
from futures_trading.trading.infrastructure.bar_aggregator import BarAggregator

T0 = datetime(2026, 10, 16, 9, 30, tzinfo=UTC)


def tick(ts: datetime, price: str, volume: str = "1", oi: str = "10") -> Tick:
    return Tick(
        symbol="rb",
        ts=ts,
        last_price=Price(Decimal(price)),
        volume=Quantity(Decimal(volume)),
        open_interest=Quantity(Decimal(oi)),
    )


class TestAggregation:
    def test_single_period(self) -> None:
        agg = BarAggregator("rb", 15)
        assert agg.on_tick(tick(T0, "3500")) is None
        assert agg.on_tick(tick(T0 + timedelta(minutes=1), "3510")) is None
        assert agg.on_tick(tick(T0 + timedelta(minutes=2), "3505")) is None
        bar = agg.flush()
        assert bar is not None
        assert bar.open == Price(Decimal("3500"))
        assert bar.high == Price(Decimal("3510"))
        assert bar.low == Price(Decimal("3500"))
        assert bar.close == Price(Decimal("3505"))
        assert bar.volume == Quantity(Decimal("3"))
        assert bar.ts == T0

    def test_period_boundary_closes_bar(self) -> None:
        agg = BarAggregator("rb", 15)
        agg.on_tick(tick(T0, "3500"))
        agg.on_tick(tick(T0 + timedelta(minutes=14), "3510"))
        # 第 15 分钟 tick 触发上一根闭合
        closed = agg.on_tick(tick(T0 + timedelta(minutes=15), "3520"))
        assert closed is not None
        assert closed.open == Price(Decimal("3500"))
        assert closed.close == Price(Decimal("3510"))
        assert closed.ts == T0
        # 新周期开始
        bar = agg.flush()
        assert bar is not None
        assert bar.open == Price(Decimal("3520"))
        assert bar.ts == T0 + timedelta(minutes=15)

    def test_volume_and_oi_accumulate(self) -> None:
        agg = BarAggregator("rb", 15)
        agg.on_tick(tick(T0, "3500", volume="5", oi="100"))
        agg.on_tick(tick(T0 + timedelta(minutes=1), "3501", volume="3", oi="50"))
        bar = agg.flush()
        assert bar is not None
        assert bar.volume == Quantity(Decimal("8"))
        assert bar.open_interest == Quantity(Decimal("150"))

    def test_flush_empty_returns_none(self) -> None:
        agg = BarAggregator("rb", 15)
        assert agg.flush() is None

    def test_bucket_floors_to_interval(self) -> None:
        agg = BarAggregator("rb", 30)
        # 09:47 → 周期起点 09:30
        assert agg._bucket_of(datetime(2026, 10, 16, 9, 47)) == datetime(2026, 10, 16, 9, 30)

    def test_invalid_interval(self) -> None:
        with pytest.raises(ValueError, match="必须为正"):
            BarAggregator("rb", 0)
