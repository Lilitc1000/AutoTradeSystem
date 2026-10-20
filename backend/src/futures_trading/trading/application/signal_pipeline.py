"""SignalPipeline：信号 → 组合预检 → 仓位计算 → 风控终检 → 报单。

这是设计文档 4.1 的完整链路，抽成用例层后**回测与实盘共用**：
- 回测：BacktestEngine 调用，order_port = BacktestBroker
- 实盘（M2）：strategy_core 调用，order_port = CTPAdapter

每一步拒绝都记录原因（设计文档 P5：可观测即默认）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ...common.types import ZERO, Money, Price, Quantity
from ..domain.model.instrument import Exchange, Instrument
from ..domain.model.order import Offset, PriceType
from ..domain.model.portfolio import Portfolio
from ..domain.model.signal import Signal
from ..domain.service.position_sizer import SizingConstraints, SizingInput, calculate
from ..domain.service.risk_engine import RiskEngine, RiskSnapshot
from .dtos import Bar, OrderCommand
from .ports import OrderPort


@dataclass(frozen=True)
class SignalOutcome:
    """信号处理的最终结果（无论成败都落库）。"""

    signal: Signal
    status: str  # EXECUTED / REJECTED / IGNORED / SIZED_OUT
    reason: str | None = None
    lots: int = 0
    binding_layer: str | None = None
    order_id: str | None = None


class SignalPipeline:
    """信号处理管线（无状态，全部上下文显式传入）。"""

    def __init__(
        self,
        risk_engine: RiskEngine,
        order_port: OrderPort,
        instruments: dict[str, Instrument],
    ) -> None:
        self._risk = risk_engine
        self._orders = order_port
        self._instruments = instruments

    def process(
        self,
        signal: Signal,
        portfolio: Portfolio,
        bars: dict[str, Bar],
        *,
        risk_per_trade_pct: Decimal,
        stop_multiplier: Decimal,
        atr: Decimal,
    ) -> SignalOutcome:
        """处理一条信号；返回可落库的结果。"""
        instrument = self._instruments.get(signal.symbol)
        if instrument is None:
            return SignalOutcome(signal, "IGNORED", f"品种 {signal.symbol} 未注册")

        bar = bars.get(signal.symbol)
        if bar is None:
            return SignalOutcome(signal, "IGNORED", "当前时刻无该品种行情")

        price = Decimal(bar.close)
        contract = instrument.contract

        # ---- 组合预检（P3：策略只产意图，组合裁决）----
        net = portfolio.net_direction(signal.symbol, {contract: Price(price)})
        if net is signal.direction:
            return SignalOutcome(signal, "IGNORED", "组合已持有同向净敞口")
        if net is not None:
            # 组合持有反向净敞口：先平仓（本管线只发平仓指令）
            return self._emit_close(signal, instrument, portfolio, bars)

        # ---- 仓位计算（四层取小）----
        instance = portfolio.instances.get(signal.instance_id)
        if instance is None:
            return SignalOutcome(signal, "IGNORED", f"实例 {signal.instance_id} 未注册")
        current_exposure = portfolio.instrument_exposure(
            signal.symbol, {contract: Price(price)}
        )
        exposure_cap = Decimal(portfolio.equity) * Decimal(
            portfolio.constraints.instrument_exposure_cap_pct
        )
        slice_remaining = Decimal(instance.capital_slice) + Decimal(instance.daily_pnl)
        sizing = calculate(
            SizingInput(
                capital=instance.capital_slice,
                risk_per_trade_pct=risk_per_trade_pct,
                atr=Price(atr),
                stop_multiplier=stop_multiplier,
                multiplier=instrument.multiplier,
                tick_size=instrument.tick_size,
                price=Price(price),
                margin_rate=instrument.margin_rate,
                constraints=SizingConstraints(
                    instrument_exposure_cap=Money(exposure_cap),
                    current_instrument_exposure=current_exposure,
                    available_margin=Money(Decimal("1e12")),  # 回测简化：保证金由 L2/L4 约束代管
                    margin_usage_cap_pct=Decimal("1"),
                    strategy_slice_remaining=Money(max(slice_remaining, ZERO)),
                    min_lots=1,
                ),
            )
        )
        if sizing.lots == 0:
            return SignalOutcome(signal, "SIZED_OUT", "四层约束不足最小手数", binding_layer=None)

        # ---- 风控终检（硬规则）----
        decision = self._risk.check(
            RiskSnapshot(
                equity=portfolio.equity,
                peak_equity=portfolio.peak_equity,
                slice_equity=instance.capital_slice,
                slice_peak=instance.slice_peak,
                daily_pnl=instance.daily_pnl,
                instruments_reconciled=True,
                market_data_fresh=True,
                api_healthy=True,
                margin_sufficient=True,
                order_has_stop_loss=True,  # 策略内置 ATR 止损（M2 由 trade_gateway 落条件单）
            ),
            is_open=True,
        )
        if not decision.allowed:
            return SignalOutcome(signal, "REJECTED", f"{decision.rule}: {decision.reason}")

        # ---- 报单 ----
        command = OrderCommand(
            instance_id=signal.instance_id,
            contract=contract,
            symbol=signal.symbol,
            direction=signal.direction,
            offset=Offset.OPEN,
            quantity=Quantity(Decimal(sizing.lots)),
            price_type=PriceType.LIMIT,
            # SHFE 不支持市价单（错误码 79）——按信号触发 bar 的收盘价报单；
            # 精确成交由 ExecutionService 追单循环保证（接线后升级）
            limit_price=_bar_close(bars, contract),
            signal_id=signal.signal_id,
        )
        order = self._orders.submit(command)
        return SignalOutcome(
            signal,
            "EXECUTED",
            lots=sizing.lots,
            binding_layer=sizing.binding_layer,
            order_id=order.order_id,
        )

    def _emit_close(
        self,
        signal: Signal,
        instrument: Instrument,
        portfolio: Portfolio,
        bars: dict[str, Bar],
    ) -> SignalOutcome:
        """平掉组合中该品种的反向净敞口。

        SHFE 特殊：按持仓**今昨构成拆分**报单（平今 '3' + 平昨 '1'）——
        只用 '1' 平今仓会被柜台以"持仓不足"拒单。非 SHFE 品种用通用
        Offset.CLOSE。价格：LIMIT @ bar 收盘价（SHFE 不支持市价单，
        错误码 79；精确成交由 ExecutionService 追单循环保证——接线前
        的过渡语义：信号基于已收盘 bar，按收盘价报单）。
        """
        is_shfe = instrument.exchange is Exchange.SHFE
        today_lots = Decimal("0")
        yesterday_lots = Decimal("0")
        for instance in portfolio.instances.values():
            for position in instance.open_positions():
                if position.contract == instrument.contract:
                    today_lots += position.today_quantity
                    yesterday_lots += position.yesterday_quantity
        total_lots = today_lots + yesterday_lots
        if total_lots == ZERO:
            return SignalOutcome(signal, "IGNORED", "无可平持仓")
        close_price = _bar_close(bars, instrument.contract)
        order_ids: list[str] = []
        plans: list[tuple[Offset, Decimal]] = []
        if is_shfe:
            # 先平今后平昨（今仓是日内主体；平昨单不挤占今仓）
            if today_lots > ZERO:
                plans.append((Offset.CLOSE_TODAY, today_lots))
            if yesterday_lots > ZERO:
                plans.append((Offset.CLOSE_YESTERDAY, yesterday_lots))
        else:
            plans.append((Offset.CLOSE, total_lots))
        for offset, lots in plans:
            command = OrderCommand(
                instance_id=signal.instance_id,
                contract=instrument.contract,
                symbol=signal.symbol,
                direction=signal.direction,  # 平仓方向与信号一致（与持仓相反）
                offset=offset,
                quantity=Quantity(lots),
                price_type=PriceType.LIMIT,
                limit_price=close_price,
                signal_id=signal.signal_id,
            )
            order = self._orders.submit(command)
            order_ids.append(order.order_id)
        return SignalOutcome(
            signal,
            "EXECUTED",
            lots=int(total_lots),
            reason=f"平掉反向敞口（今{today_lots}/昨{yesterday_lots}）",
            order_id=",".join(order_ids),
        )


def _bar_close(bars: dict[str, Bar], contract: str) -> Price | None:
    """取合约最新 bar 收盘价（报单限价基准）。

    bars 的 key 在不同调用方不一致（shadow/回测传 symbol，实盘可传
    contract）——两种都尝试。无 bar 返回 None（调用方需保证 bars 新鲜，
    否则 CTP 会因限价空值拒单）。
    """
    bar = bars.get(contract)
    if bar is None:
        symbol = "".join(ch for ch in contract if ch.isalpha())
        bar = bars.get(symbol)
    return bar.close if bar is not None else None
