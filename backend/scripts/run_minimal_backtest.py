"""最小回测闭环演示：一根 15 分钟 K 线的完整旅程。

链路（设计文档 4.1）：
  行情 bar → 策略信号 → 组合预检 → 仓位计算（四层取小）
  → 风控终检 → OrderCommand → BacktestBroker 撮合 → 记账

使用确定性合成数据（固定随机种子），无需网络与外部数据源，
保证任何环境可重复运行。
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from futures_trading.common.types import ZERO, Money, Price, Quantity  # noqa: E402
from futures_trading.trading.application.dtos import Bar, OrderCommand  # noqa: E402
from futures_trading.trading.domain.model.instrument import Exchange, Instrument  # noqa: E402
from futures_trading.trading.domain.model.order import Offset, PriceType  # noqa: E402
from futures_trading.trading.domain.model.signal import Direction, Signal  # noqa: E402
from futures_trading.trading.domain.model.strategy import (  # noqa: E402
    InstanceMode,
    StrategyInstance,
)
from futures_trading.trading.domain.service.position_sizer import (  # noqa: E402
    SizingConstraints,
    SizingInput,
    calculate,
)
from futures_trading.trading.domain.service.risk_engine import RiskEngine, RiskSnapshot  # noqa: E402
from futures_trading.trading.infrastructure.backtest.backtest_broker import BacktestBroker  # noqa: E402
from futures_trading.trading.infrastructure.backtest.slippage_model import SlippageModel  # noqa: E402

INTERVAL_MINUTES = 15
BAR_COUNT = 600
START_PRICE = Decimal("3500")


def synthesize_bars(count: int) -> list[Bar]:
    """确定性合成 15 分钟 K 线：随机游走 + 两段趋势。"""
    rng = random.Random(42)
    bars: list[Bar] = []
    price = START_PRICE
    ts = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    for i in range(count):
        drift = Decimal("0.6") if 150 <= i < 400 else Decimal("-0.2")  # 中段趋势
        change = Decimal(str(rng.gauss(float(drift), 6.0)))
        open_price = price
        close_price = max(Decimal("1"), open_price + change)
        high = max(open_price, close_price) + Decimal(str(abs(rng.gauss(0, 2))))
        low = min(open_price, close_price) - Decimal(str(abs(rng.gauss(0, 2))))
        bars.append(
            Bar(
                symbol="rb",
                interval_minutes=INTERVAL_MINUTES,
                ts=ts,
                open=Price(open_price),
                high=Price(high),
                low=Price(low),
                close=Price(close_price),
                volume=Quantity(Decimal(str(rng.randint(500, 3000)))),
            )
        )
        price = close_price
        ts += timedelta(minutes=INTERVAL_MINUTES)
    return bars


def moving_average(bars: list[Bar], end: int, window: int) -> Decimal:
    window_bars = bars[max(0, end - window + 1) : end + 1]
    if not window_bars:
        return ZERO
    return sum((Decimal(b.close) for b in window_bars), ZERO) / Decimal(len(window_bars))


def main() -> None:
    instrument = Instrument(
        symbol="rb",
        contract="rb2510",
        name="螺纹钢",
        exchange=Exchange.SHFE,
        multiplier=Decimal("10"),
        tick_size=Decimal("1"),
        margin_rate=Decimal("0.12"),
    )
    instance = StrategyInstance(
        instance_id="demo-1",
        strategy_id="trend_atr_v1",
        param_version=1,
        mode=InstanceMode.SHADOW,
        capital_slice=Money(Decimal("100000")),
        slice_peak=Money(Decimal("100000")),
    )
    broker = BacktestBroker(
        initial_capital=Money(Decimal("1000000")),
        slippage=SlippageModel(ticks=Decimal("1"), commission_per_lot=Money(Decimal("10"))),
        instruments={instrument.contract: instrument},
    )
    risk_engine = RiskEngine()

    bars = synthesize_bars(BAR_COUNT)
    position: int = 0  # 当前持仓手数（>0 多，<0 空，0 空仓）
    entry_price: Decimal | None = None

    print(f"合成数据：{len(bars)} 根 {INTERVAL_MINUTES} 分钟 K 线，品种 rb（乘数 {instrument.multiplier}）")
    print("策略：双均线交叉（SMA5 上穿 SMA20 做多，下穿做空），ATR 近似仓位 + 四层取小 + 风控终检\n")

    for i, bar in enumerate(bars):
        broker.on_bar(bar)
        if i < 25:
            continue

        fast = moving_average(bars, i, 5)
        slow = moving_average(bars, i, 20)
        prev_fast = moving_average(bars, i - 1, 5)
        prev_slow = moving_average(bars, i - 1, 20)

        signal: Signal | None = None
        if prev_fast <= prev_slow and fast > slow and position <= 0:
            signal = Signal(instance.instance_id, "rb", Direction.LONG, bar.ts, reason="SMA5 上穿 SMA20")
        elif prev_fast >= prev_slow and fast < slow and position >= 0:
            signal = Signal(instance.instance_id, "rb", Direction.SHORT, bar.ts, reason="SMA5 下穿 SMA20")
        if signal is None:
            continue

        # 平仓反向持仓
        if position != 0:
            close_direction = Direction.SHORT if position > 0 else Direction.LONG
            command = OrderCommand(
                instance_id=instance.instance_id,
                contract=instrument.contract,
                symbol="rb",
                direction=close_direction,
                offset=Offset.CLOSE,
                quantity=Quantity(Decimal(abs(position))),
                price_type=PriceType.MARKET,
                signal_id=signal.signal_id,
            )
            broker.submit(command)

        # ATR 近似：最近 20 根 bar 的平均 (high-low)
        atr = sum((Decimal(b.high) - Decimal(b.low) for b in bars[i - 19 : i + 1]), ZERO) / Decimal(20)

        sizing = calculate(
            SizingInput(
                capital=instance.capital_slice,
                risk_per_trade_pct=Decimal("0.01"),
                atr=Price(atr),
                stop_multiplier=Decimal("2"),
                multiplier=instrument.multiplier,
                tick_size=instrument.tick_size,
                price=bar.close,
                margin_rate=instrument.margin_rate,
                constraints=SizingConstraints(
                    instrument_exposure_cap=Money(Decimal("200000")),
                    current_instrument_exposure=Money(ZERO),
                    available_margin=Money(Decimal("500000")),
                    margin_usage_cap_pct=Decimal("0.5"),
                    strategy_slice_remaining=instance.capital_slice,
                    min_lots=1,
                ),
            )
        )
        if sizing.lots == 0:
            print(f"[{bar.ts:%m-%d %H:%M}] 信号被仓位计算否决（四层约束不足最小手数）")
            continue

        # 风控终检
        decision = risk_engine.check(
            RiskSnapshot(
                equity=broker.equity({instrument.contract: bar.close}),
                peak_equity=Money(Decimal("1000000")),
                slice_equity=instance.capital_slice,
                slice_peak=instance.slice_peak,
                daily_pnl=Money(ZERO),
                instruments_reconciled=True,
                market_data_fresh=True,
                api_healthy=True,
                margin_sufficient=True,
                order_has_stop_loss=True,
            ),
            is_open=True,
        )
        if not decision.allowed:
            print(f"[{bar.ts:%m-%d %H:%M}] 信号被风控拒绝：{decision.rule} {decision.reason}")
            continue

        command = OrderCommand(
            instance_id=instance.instance_id,
            contract=instrument.contract,
            symbol="rb",
            direction=signal.direction,
            offset=Offset.OPEN,
            quantity=Quantity(Decimal(sizing.lots)),
            price_type=PriceType.MARKET,
            signal_id=signal.signal_id,
        )
        broker.submit(command)
        position = sizing.lots if signal.direction is Direction.LONG else -sizing.lots
        entry_price = bar.close
        print(
            f"[{bar.ts:%m-%d %H:%M}] {signal.direction.value:5s} {sizing.lots} 手 @ ~{bar.close} "
            f"(binding={sizing.binding_layer}, reason={signal.reason})"
        )

    # 收盘记录
    last_bar = bars[-1]
    final_equity = broker.equity({instrument.contract: last_bar.close})
    pnl = final_equity - Decimal(broker.initial_capital)
    print("\n===== 回测结果 =====")
    print(f"初始资金: {broker.initial_capital:>12,.2f}")
    print(f"期末权益: {final_equity:>12,.2f}")
    print(f"总盈亏:   {pnl:>+12,.2f}  ({pnl / Decimal(broker.initial_capital):+.2%})")
    print(f"成交笔数: {len(broker.trade_log)}")
    print(f"末笔持仓: {position} 手 @ {entry_price}")


if __name__ == "__main__":
    main()
