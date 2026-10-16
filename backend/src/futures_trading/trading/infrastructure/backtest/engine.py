"""BacktestEngine：事件驱动回测主循环。

每个时间戳的执行顺序（对齐设计文档 4.1 的实盘链路）：
  1. clock/market_data 推进到当前时刻
  2. 各实例的策略产生 Signal（只能看到当前及以前的 bar——防未来函数）
  3. SignalPipeline：组合预检 → 仓位计算 → 风控终检 → 报单
  4. BacktestBroker 按当前 bar 撮合挂单
  5. 成交回报更新实例持仓 / 当日盈亏 / 权益曲线
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ....common.types import ZERO, Money, Price, Quantity
from ...application.dtos import Bar
from ...application.signal_pipeline import SignalOutcome, SignalPipeline
from ...application.strategy import SignalGenerator, make_generator
from ...domain.model.instrument import Instrument
from ...domain.model.order import Offset, Order
from ...domain.model.portfolio import Portfolio, PortfolioConstraints
from ...domain.model.position import Fill
from ...domain.model.strategy import InstanceMode, StrategyInstance, StrategyType
from ...domain.service.risk_engine import RiskEngine
from .backtest_broker import BacktestBroker
from .clock import BacktestClock
from .market_data import BacktestMarketData
from .slippage_model import SlippageModel


@dataclass(frozen=True)
class BacktestConfig:
    """回测配置（一次运行的完整快照，保证可复现）。"""

    strategy_type: StrategyType = StrategyType.TREND_ATR
    param_version: int = 1
    universe: tuple[str, ...] = ()
    params: dict[str, object] = field(default_factory=dict)
    initial_capital: Decimal = Decimal("1000000")
    risk_per_trade_pct: Decimal = Decimal("0.01")
    stop_multiplier: Decimal = Decimal("2")
    constraints: PortfolioConstraints = field(default_factory=PortfolioConstraints)
    slippage_ticks: Decimal = Decimal("1")
    commission_per_lot: Decimal = Decimal("10")


@dataclass
class BacktestResult:
    """回测产出（供 metrics 与 report 消费）。"""

    equity_curve: list[tuple[datetime, Decimal]] = field(default_factory=list)
    trades: list[dict[str, object]] = field(default_factory=list)
    outcomes: list[SignalOutcome] = field(default_factory=list)
    final_equity: Decimal = ZERO
    bars_processed: int = 0


class BacktestEngine:
    """回测引擎（单实例单组合的 M1 范围；多实例循环同构）。"""

    def __init__(
        self,
        config: BacktestConfig,
        market_data: BacktestMarketData,
        broker: BacktestBroker,
        instruments: dict[str, Instrument],
        clock: BacktestClock | None = None,
    ) -> None:
        self.config = config
        self.market_data = market_data
        self.broker = broker
        self.instruments = instruments
        self.clock = clock or BacktestClock(market_data.timeline[0])
        self.risk_engine = RiskEngine()
        self.pipeline = SignalPipeline(self.risk_engine, broker, instruments)

        self.instance = StrategyInstance(
            instance_id="bt-1",
            strategy_id=config.strategy_type.value.lower(),
            param_version=config.param_version,
            mode=InstanceMode.SIM,
            capital_slice=Money(config.initial_capital),
            slice_peak=Money(config.initial_capital),
        )
        self.portfolio = Portfolio(
            equity=Money(config.initial_capital),
            peak_equity=Money(config.initial_capital),
            constraints=config.constraints,
        )
        self.portfolio.register(self.instance)
        self.generator: SignalGenerator = make_generator(
            config.strategy_type.value, config.params
        )

    # ---- 主循环 ----

    def run(self) -> BacktestResult:
        result = BacktestResult()
        ts: datetime | None = self.market_data.at_beginning()
        history: dict[str, list[Bar]] = {symbol: [] for symbol in self.market_data.symbols}
        current_day = ts.date() if ts else None

        while ts is not None:
            self.clock.advance(ts)
            bars_now = self.market_data.bars_at_current()
            for symbol, bar in bars_now.items():
                history[symbol].append(bar)

            # 跨自然日重置当日盈亏（单日亏损限额按日计——不清零会误拒后续所有开仓）
            if current_day is not None and ts.date() != current_day:
                self.instance.daily_pnl = Money(ZERO)
            current_day = ts.date()

            # 2. 策略信号（历史窗口截至当前时刻）
            signals = self.generator.on_bar(history, self.instance, ts)

            # 3. 信号管线（组合预检 → 仓位 → 风控 → 报单）
            for signal in signals:
                atr = self._atr_of(history.get(signal.symbol, []))
                outcome = self.pipeline.process(
                    signal,
                    self.portfolio,
                    bars_now,
                    risk_per_trade_pct=self.config.risk_per_trade_pct,
                    stop_multiplier=self.config.stop_multiplier,
                    atr=atr,
                )
                result.outcomes.append(outcome)

            # 4. 撮合（本时刻挂单按当前 bar 成交）
            filled = self.broker.on_bar_all(bars_now)
            self._apply_fills(filled)

            # 5. 记账
            last_prices = {symbol: bar.close for symbol, bar in bars_now.items()}
            equity = self.broker.equity(last_prices)
            self.portfolio.equity = equity
            self.portfolio.update_peak()
            result.equity_curve.append((ts, Decimal(equity)))
            result.trades = list(self.broker.trade_log)
            result.bars_processed += 1

            ts = self.market_data.advance()

        result.final_equity = Decimal(self.broker.equity(self._last_prices()))
        return result

    # ---- 内部 ----

    def _apply_fills(self, filled: list[Order]) -> None:
        for order in filled:
            if order.avg_fill_price is None:
                continue
            instrument = self.instruments.get(order.symbol)
            if instrument is None:
                continue
            fill = Fill(
                quantity=Quantity(order.filled_quantity),
                price=Price(order.avg_fill_price),
            )
            if order.offset is Offset.OPEN:
                position = self.instance.position_for(
                    order.contract, order.direction, instrument.multiplier
                )
                position.add(fill)
            else:
                existing = self.instance.positions.get(order.contract)
                if existing is not None and not existing.is_flat():
                    realized = existing.reduce(fill)
                    self.instance.daily_pnl = Money(
                        Decimal(self.instance.daily_pnl) + Decimal(realized)
                    )
                    if existing.is_flat():
                        self.instance.close_position(order.contract)

    def _last_prices(self) -> dict[str, Price]:
        prices: dict[str, Price] = {}
        for symbol in self.market_data.symbols:
            bar = self.market_data.latest_bar(symbol, 0)
            if bar is not None:
                prices[symbol] = bar.close
        return prices

    @staticmethod
    def _atr_of(history: list[Bar]) -> Decimal:
        if len(history) < 2:
            return Decimal("1")  # 无足够历史时给非零值，让仓位计算可用
        trs = [
            max(
                Decimal(b.high) - Decimal(b.low),
                abs(Decimal(b.high) - Decimal(b.close)),
                abs(Decimal(b.low) - Decimal(b.close)),
            )
            for b in history[-20:]
        ]
        atr = sum(trs, Decimal("0")) / Decimal(len(trs))
        return atr if atr > ZERO else Decimal("1")


__all__ = ["BacktestConfig", "BacktestResult", "BacktestEngine", "SlippageModel"]
