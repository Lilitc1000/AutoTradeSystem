"""BacktestEngine 测试：合成数据端到端 + 可复现性。"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from futures_trading.common.instrument_registry import get_instrument
from futures_trading.trading.application.dtos import Bar
from futures_trading.trading.infrastructure.backtest.backtest_broker import BacktestBroker
from futures_trading.trading.infrastructure.backtest.clock import BacktestClock
from futures_trading.trading.infrastructure.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
)
from futures_trading.trading.infrastructure.backtest.market_data import BacktestMarketData

START = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def synthesize(symbol: str, count: int = 300, seed: int = 42, start_day: int = 0) -> list[Bar]:
    """确定性合成日线：两段趋势 + 噪声。"""
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = Decimal("3500")
    ts = START + timedelta(days=start_day)
    for i in range(count):
        drift = Decimal("1.2") if 80 <= i < 200 else Decimal("-0.4")
        change = Decimal(str(rng.gauss(float(drift), 8.0)))
        open_price = price
        close = max(Decimal("1"), open_price + change)
        bars.append(
            Bar(
                symbol=symbol,
                interval_minutes=1440,
                ts=ts,
                open=open_price,
                high=max(open_price, close) + Decimal(str(abs(rng.gauss(0, 3)))),
                low=min(open_price, close) - Decimal(str(abs(rng.gauss(0, 3)))),
                close=close,
                volume=Decimal(str(rng.randint(1000, 5000))),
            )
        )
        price = close
        ts += timedelta(days=1)
    return bars


@pytest.fixture
def bars() -> dict[str, list[Bar]]:
    return {"rb": synthesize("rb")}


@pytest.fixture
def instruments() -> dict[str, object]:
    return {"rb": get_instrument("rb")}


def make_engine(
    bars: dict[str, list[Bar]],
    instruments: dict[str, object],
    **param_overrides: object,
) -> BacktestEngine:
    from futures_trading.trading.domain.model.strategy import StrategyType

    params = {"fast_window": 5, "slow_window": 20, "atr_window": 20}
    params.update(param_overrides)
    config = BacktestConfig(
        strategy_type=StrategyType.TREND_ATR,
        param_version=1,
        universe=("rb",),
        params=params,
        initial_capital=Decimal("1000000"),
        risk_per_trade_pct=Decimal("0.01"),
    )
    market_data = BacktestMarketData(bars)
    broker = BacktestBroker(
        initial_capital=Decimal("1000000"),
        instruments=instruments,  # type: ignore[arg-type]
    )
    return BacktestEngine(config, market_data, broker, instruments)  # type: ignore[arg-type]


class TestEngineEndToEnd:
    def test_runs_and_produces_curve(
        self, bars: dict[str, list[Bar]], instruments: dict[str, object]  # noqa: E501
    ) -> None:
        engine = make_engine(bars, instruments)
        result = engine.run()
        assert result.bars_processed == len(bars["rb"])
        assert len(result.equity_curve) == len(bars["rb"])
        # 时间单调
        timestamps = [ts for ts, _ in result.equity_curve]
        assert timestamps == sorted(timestamps)

    def test_reproducible(
        self, bars: dict[str, list[Bar]], instruments: dict[str, object]  # noqa: E501
    ) -> None:
        first = make_engine(bars, instruments).run()
        second = make_engine(bars, instruments).run()
        assert [e for _, e in first.equity_curve] == [e for _, e in second.equity_curve]
        assert len(first.trades) == len(second.trades)

    def test_trend_segment_produces_trades(
        self, bars: dict[str, list[Bar]], instruments: dict[str, object]  # noqa: E501
    ) -> None:
        result = make_engine(bars, instruments).run()
        # 合成数据有趋势段，应至少产生一次信号处理
        assert len(result.outcomes) > 0
        statuses = {o.status for o in result.outcomes}
        assert statuses <= {"EXECUTED", "REJECTED", "IGNORED", "SIZED_OUT"}

    def test_no_lookahead(self) -> None:
        """时钟回滚必须抛错（未来函数防护）。"""
        clock = BacktestClock(START)
        clock.advance(START + timedelta(days=1))
        with pytest.raises(ValueError, match="时钟回滚被禁止"):
            clock.advance(START)


class TestMarketDataAlignment:
    def test_timeline_is_union(self) -> None:
        bars_a = synthesize("a", count=3, seed=1, start_day=0)
        bars_b = synthesize("b", count=2, seed=2, start_day=10)  # 错峰 → 日历差异
        market_data = BacktestMarketData({"a": bars_a, "b": bars_b})
        assert len(market_data.timeline) == 5  # 3 + 2，时间戳不重叠

    def test_latest_bar_tracks_cursor(self) -> None:
        bars_a = synthesize("a", count=3, seed=1)
        market_data = BacktestMarketData({"a": bars_a})
        market_data.at_beginning()
        assert market_data.latest_bar("a", 1440) is not None
        assert market_data.advance() is not None
        assert market_data.latest_bar("a", 1440).ts == bars_a[1].ts  # type: ignore[union-attr]

    def test_empty_rejected(self) -> None:
        from futures_trading.common.errors import ValidationError

        with pytest.raises(ValidationError, match="不能为空"):
            BacktestMarketData({})
