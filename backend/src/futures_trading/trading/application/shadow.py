"""影子引擎：实盘链路（不含 IO），回测与实盘同构的最终拼图。

职责：维护各品种 bar 历史窗口 → 策略产信号 → SignalPipeline 全链路 →
影子成交（OrderPort = BacktestBroker 虚拟账户）。进程壳（strategy_core）
只负责 Redis 总线 IO，本类不碰网络，可完整单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ...common.types import Money
from ..application.dtos import Bar
from ..application.ports import OrderPort
from ..application.signal_pipeline import SignalOutcome, SignalPipeline
from ..application.strategy import SignalGenerator, make_generator
from ..domain.model.instrument import Instrument
from ..domain.model.order import Offset
from ..domain.model.portfolio import Portfolio, PortfolioConstraints
from ..domain.model.position import Fill
from ..domain.model.strategy import InstanceMode, StrategyInstance, StrategyType
from ..domain.service.risk_engine import RiskEngine
from ..infrastructure.backtest.backtest_broker import BacktestBroker
from ..infrastructure.backtest.slippage_model import SlippageModel


@dataclass(frozen=True)
class ShadowConfig:
    """影子实例配置（M2-a 由 env/配置驱动；M2-b 起从 PG strategy_instances 加载）。"""

    instance_id: str = "shadow-1"
    strategy_type: StrategyType = StrategyType.TREND_ATR
    param_version: int = 1
    params: dict[str, object] = field(default_factory=dict)
    initial_capital: Decimal = Decimal("1000000")
    risk_per_trade_pct: Decimal = Decimal("0.01")
    stop_multiplier: Decimal = Decimal("2")
    slippage_ticks: Decimal = Decimal("1")
    commission_per_lot: Decimal = Decimal("10")


class ShadowEngine:
    """单影子实例的实盘链路（无 IO）。"""

    def __init__(
        self,
        config: ShadowConfig,
        instruments: dict[str, Instrument],
        order_port: OrderPort | None = None,
        external_fills: bool = False,
    ) -> None:
        """external_fills=True 为 SIM 模式：成交由柜台异步回报喂入（非同步撮合）。"""
        self.config = config
        self.instruments = instruments
        self.external_fills = external_fills
        self.history: dict[str, list[Bar]] = {}
        self.generator: SignalGenerator = make_generator(config.strategy_type.value, config.params)
        self.risk_engine = RiskEngine()
        # 影子成交：默认虚拟账户（BacktestBroker 即 OrderPort 完整实现）
        self.broker: BacktestBroker = order_port or BacktestBroker(  # type: ignore[assignment]  # 影子成交需撮合能力
            initial_capital=Money(config.initial_capital),
            slippage=SlippageModel(
                ticks=config.slippage_ticks,
                commission_per_lot=Money(config.commission_per_lot),
            ),
            instruments=instruments,
        )
        self.pipeline = SignalPipeline(self.risk_engine, self.broker, instruments)
        self.instance = StrategyInstance(
            instance_id=config.instance_id,
            strategy_id=config.strategy_type.value.lower(),
            param_version=config.param_version,
            mode=InstanceMode.SHADOW,
            capital_slice=Money(config.initial_capital),
            slice_peak=Money(config.initial_capital),
        )
        self.portfolio = Portfolio(
            equity=Money(config.initial_capital),
            peak_equity=Money(config.initial_capital),
            constraints=PortfolioConstraints(),
        )
        self.portfolio.register(self.instance)
        self.outcomes: list[SignalOutcome] = []

    def on_bar(self, bar: Bar) -> list[SignalOutcome]:
        """喂入一根 bar（来自 CTP 或回放）；返回本根 bar 产生的信号处理结果。"""
        self.history.setdefault(bar.symbol, []).append(bar)

        # 1. 策略信号（只看当前及以前——防未来函数同样适用于实盘）
        signals = self.generator.on_bar(self.history, self.instance, bar.ts)
        results: list[SignalOutcome] = []

        for signal in signals:
            atr = _atr_of(self.history.get(signal.symbol, []))
            outcome = self.pipeline.process(
                signal,
                self.portfolio,
                {bar.symbol: bar},
                risk_per_trade_pct=self.config.risk_per_trade_pct,
                stop_multiplier=self.config.stop_multiplier,
                atr=atr,
            )
            results.append(outcome)
            self.outcomes.append(outcome)

        # 2. 成交：影子模式同步撮合；SIM 模式由柜台回报异步喂入（见 apply_fill）
        if not self.external_fills:
            filled = self.broker.on_bar(bar)
            self._apply_fills(filled)

            # 3. 记账（影子：虚拟账户权益）
            last_prices = {s: bars[-1].close for s, bars in self.history.items()}
            self.portfolio.equity = self.broker.equity(last_prices)
            self.portfolio.update_peak()
        return results

    def apply_fill(self, order: object) -> None:
        """SIM 模式：应用一笔柜台成交回报（OnRtnTrade 后的 Order）。"""
        self._apply_fills([order])

    def set_equity(self, equity: Decimal) -> None:
        """SIM 模式：用柜台资金快照更新组合权益。"""
        self.portfolio.equity = Money(Decimal(equity))
        self.portfolio.update_peak()

    def _apply_fills(self, filled: list) -> None:
        for order in filled:
            if order.avg_fill_price is None:
                continue
            instrument = self.instruments.get(order.symbol)
            if instrument is None:
                continue
            fill = Fill(quantity=order.filled_quantity, price=order.avg_fill_price)
            if order.offset is Offset.OPEN:
                position = self.instance.position_for(
                    order.contract, order.direction, instrument.multiplier
                )
                position.add(fill)
            else:
                existing = self.instance.positions.get(order.contract)
                if existing is not None and not existing.is_flat():
                    realized = existing.reduce(fill)
                    self.instance.daily_pnl = Money(Decimal(self.instance.daily_pnl) + Decimal(realized))
                    if existing.is_flat():
                        self.instance.close_position(order.contract)


def _atr_of(history: list[Bar]) -> Decimal:
    if len(history) < 2:
        return Decimal("1")
    trs = [
        max(
            Decimal(b.high) - Decimal(b.low),
            abs(Decimal(b.high) - Decimal(b.close)),
            abs(Decimal(b.low) - Decimal(b.close)),
        )
        for b in history[-20:]
    ]
    atr = sum(trs, Decimal("0")) / Decimal(len(trs))
    return atr if atr > Decimal("0") else Decimal("1")


__all__ = ["ShadowConfig", "ShadowEngine"]
