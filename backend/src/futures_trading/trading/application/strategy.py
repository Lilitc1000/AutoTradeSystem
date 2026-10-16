"""策略接口与实现（SignalGenerator）。

策略是纯函数式的信号产生器：输入历史 bar 窗口 + 实例参数，输出 Signal 列表。
不含下单、仓位、风控——那些在 SignalPipeline（回测实盘共用）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from ..application.dtos import Bar
from ..domain.model.signal import Direction, Signal
from ..domain.model.strategy import StrategyInstance


class SignalGenerator(ABC):
    """策略信号产生器接口。"""

    @abstractmethod
    def on_bar(
        self,
        bars: Mapping[str, list[Bar]],
        instance: StrategyInstance,
        ts: datetime,
    ) -> list[Signal]:
        """基于截至 ts 的 bar 历史，产出交易意图。"""


class TrendATRGenerator(SignalGenerator):
    """趋势跟踪 + ATR 止损（简化海 turtle，设计文档 5.1 的主线策略）。

    开仓：SMA(fast) 上穿 SMA(slow) 做多，下穿做空。
    平仓：持仓中价格跌破/升破 entry ± N × ATR（跟踪止损）。
    仓位由 PositionSizer 按 ATR 计算（单笔风险恒定）。
    """

    def __init__(
        self,
        fast_window: int = 10,
        slow_window: int = 30,
        atr_window: int = 20,
        stop_multiplier: Decimal = Decimal("2"),
    ) -> None:
        if fast_window >= slow_window:
            raise ValueError("fast_window 必须小于 slow_window")
        if atr_window < 2:
            raise ValueError("atr_window 至少为 2")
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.atr_window = atr_window
        self.stop_multiplier = stop_multiplier

    def on_bar(
        self,
        bars: Mapping[str, list[Bar]],
        instance: StrategyInstance,
        ts: datetime,
    ) -> list[Signal]:
        signals: list[Signal] = []
        for symbol, history in bars.items():
            if len(history) < self.slow_window + 1:
                continue
            window = history[-(self.slow_window + 1) :]
            fast_now = self._sma(window, self.fast_window)
            fast_prev = self._sma(window[:-1], self.fast_window)
            slow_now = self._sma(window, self.slow_window)
            slow_prev = self._sma(window[:-1], self.slow_window)
            atr = self._atr(window, self.atr_window)
            if atr <= Decimal("0"):
                continue

            # 持仓检查：先处理止损（优先于新开仓）
            position = instance.positions.get(self._contract_of(symbol, instance))
            if position is not None and not position.is_flat() and position.avg_price is not None:
                stop = self._stop_price(position.direction, Decimal(position.avg_price), atr)
                last_bar = history[-1]
                # 止损触发用 low/high（盘中触及即触发，不只看收盘——日线下收盘收回会漏检）
                touched = (
                    position.direction is Direction.LONG and Decimal(last_bar.low) <= stop
                ) or (position.direction is Direction.SHORT and Decimal(last_bar.high) >= stop)
                if touched:
                    signals.append(
                        Signal(
                            instance.instance_id,
                            symbol,
                            position.direction.opposite,
                            ts,
                            reason=f"ATR 止损 {stop:.4g}",
                        )
                    )
                    continue

            # 均线交叉开仓
            if position is not None and not position.is_flat():
                continue  # 持仓中不开新仓
            if fast_prev <= slow_prev and fast_now > slow_now:
                signals.append(Signal(instance.instance_id, symbol, Direction.LONG, ts, reason="SMA 金叉"))
            elif fast_prev >= slow_prev and fast_now < slow_now:
                signals.append(Signal(instance.instance_id, symbol, Direction.SHORT, ts, reason="SMA 死叉"))
        return signals

    def _stop_price(self, direction: Direction, entry: Decimal, atr: Decimal) -> Decimal:
        distance = atr * self.stop_multiplier
        return entry - distance if direction is Direction.LONG else entry + distance

    @staticmethod
    def _contract_of(symbol: str, instance: StrategyInstance) -> str:
        """简化：持仓 key 直接用 symbol（单合约回测；换月由数据层拼接处理）。"""
        for contract in instance.positions:
            if contract.startswith(symbol):
                return contract
        return symbol

    @staticmethod
    def _sma(window: list[Bar], period: int) -> Decimal:
        if len(window) < period:
            return Decimal("0")
        return sum((Decimal(b.close) for b in window[-period:]), Decimal("0")) / Decimal(period)

    @staticmethod
    def _atr(window: list[Bar], period: int) -> Decimal:
        if len(window) < period:
            return Decimal("0")
        trs = [
            max(
                Decimal(b.high) - Decimal(b.low),
                abs(Decimal(b.high) - Decimal(b.close)),
                abs(Decimal(b.low) - Decimal(b.close)),
            )
            for b in window[-period:]
        ]
        return sum(trs, Decimal("0")) / Decimal(period)


GENERATORS = {
    "TREND_ATR": TrendATRGenerator,
}


def make_generator(strategy_type: str, params: Mapping[str, object]) -> SignalGenerator:
    """按策略类型和参数构造信号产生器。"""
    cls = GENERATORS.get(strategy_type)
    if cls is None:
        raise ValueError(f"未知策略类型 {strategy_type}（已注册: {list(GENERATORS)}）")
    known = {"fast_window", "slow_window", "atr_window", "stop_multiplier"}
    kwargs = {k: v for k, v in params.items() if k in known}
    if "stop_multiplier" in kwargs and not isinstance(kwargs["stop_multiplier"], Decimal):
        kwargs["stop_multiplier"] = Decimal(str(kwargs["stop_multiplier"]))
    return cls(**kwargs)  # type: ignore[arg-type]
