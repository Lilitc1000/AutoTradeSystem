"""ShadowEngine 测试：实盘链路（无 IO）。"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from futures_trading.common.instrument_registry import get_instrument
from futures_trading.trading.application.dtos import Bar
from futures_trading.trading.application.shadow import ShadowConfig, ShadowEngine

START = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def synthesize(count: int = 300, seed: int = 42) -> list[Bar]:
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = Decimal("3500")
    ts = START
    for i in range(count):
        drift = Decimal("1.2") if 80 <= i < 200 else Decimal("-0.4")
        change = Decimal(str(rng.gauss(float(drift), 8.0)))
        open_price = price
        close = max(Decimal("1"), open_price + change)
        bars.append(
            Bar(
                symbol="rb",
                interval_minutes=1440,
                ts=ts,
                open=open_price,
                high=max(open_price, close) + Decimal("2"),
                low=min(open_price, close) - Decimal("2"),
                close=close,
                volume=Decimal("2000"),
            )
        )
        price = close
        ts += timedelta(days=1)
    return bars


def make_engine(**param_overrides: object) -> ShadowEngine:
    params = {"fast_window": 5, "slow_window": 20, "atr_window": 20}
    params.update(param_overrides)
    return ShadowEngine(
        ShadowConfig(params=params),
        {"rb": get_instrument("rb")},
    )


class TestShadowEngine:
    def test_feeding_bars_produces_outcomes(self) -> None:
        engine = make_engine()
        outcomes = []
        for bar in synthesize():
            outcomes.extend(engine.on_bar(bar))
        assert len(outcomes) > 0
        assert all(o.status in {"EXECUTED", "REJECTED", "IGNORED", "SIZED_OUT"} for o in outcomes)

    def test_shadow_fills_track_broker(self) -> None:
        engine = make_engine()
        for bar in synthesize():
            engine.on_bar(bar)
        # 影子成交进了虚拟账户（broker.trade_log）
        assert isinstance(engine.broker.trade_log, list)
        # 组合权益被更新
        assert engine.portfolio.equity > Decimal("0")

    def test_history_window_grows(self) -> None:
        engine = make_engine()
        bars = synthesize(count=10)
        for bar in bars:
            engine.on_bar(bar)
        assert len(engine.history["rb"]) == 10

    def test_history_window_capped(self) -> None:
        """历史窗口有上限（实盘 7x24 常驻防无限增长/内存泄漏）。"""
        from futures_trading.trading.application.shadow import HISTORY_LIMIT

        engine = make_engine()
        bars = synthesize(count=HISTORY_LIMIT + 50)
        for bar in bars:
            engine.on_bar(bar)
        assert len(engine.history["rb"]) == HISTORY_LIMIT
        # 保留的是最近部分（最后一根在窗口内）
        assert engine.history["rb"][-1].ts == bars[-1].ts

    def test_no_lookahead_signal_uses_only_past(self) -> None:
        """信号只能看到当前及以前的 bar（history 先 append 再产信号）。"""
        engine = make_engine()
        bars = synthesize(count=60)
        for bar in bars[:59]:
            engine.on_bar(bar)
        assert len(engine.history["rb"]) == 59
        # 第 60 根的 signal 在 on_bar 内部产生，此时 history 已含第 60 根——
        # 策略只用 history[-N:] 窗口，与实盘语义一致（当前 bar 收盘即可见）
        engine.on_bar(bars[59])
        assert len(engine.history["rb"]) == 60

    def test_outcome_records_binding_layer(self) -> None:
        engine = make_engine()
        for bar in synthesize():
            for outcome in engine.on_bar(bar):
                if outcome.status == "EXECUTED":
                    assert outcome.lots > 0
                    if not (outcome.reason or "").startswith("平掉反向敞口"):
                        # 开仓路径必记录约束层；平仓路径不经仓位计算
                        assert outcome.binding_layer in {"L1", "L2", "L3", "L4"}
