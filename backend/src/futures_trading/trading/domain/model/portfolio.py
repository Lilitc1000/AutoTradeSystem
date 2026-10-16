"""Portfolio 聚合根：组合层。

职责（设计文档 P3）：
- 管理全部 StrategyInstance
- 组合级约束：品种集中度、同向叠加、总回撤
- 净敞口裁决：多策略同品种信号在此汇总，超限者拒绝/降级

Portfolio 不做仓位计算（那是 PositionSizer 的职责），只回答
"这个品种还能不能加敞口、当前敞口多大"。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from ....common.errors import InvariantViolationError
from ....common.types import ZERO, Money, Price
from .instrument import is_contract_of
from .position import Position
from .signal import Direction
from .strategy import StrategyInstance


@dataclass(frozen=True)
class PortfolioConstraints:
    """组合级约束（软参数，可配置）。"""

    instrument_exposure_cap_pct: Decimal = Decimal("0.20")  # 单品种敞口上限
    total_drawdown_limit_pct: Decimal = Decimal("0.10")  # 组合总回撤熔断线
    same_direction_cap_pct: Decimal = Decimal("0.60")  # 同向叠加上限
    total_allocation_cap_pct: Decimal = Decimal("0.80")  # 切片总分配率上限

    def __post_init__(self) -> None:
        for name in (
            "instrument_exposure_cap_pct",
            "total_drawdown_limit_pct",
            "same_direction_cap_pct",
            "total_allocation_cap_pct",
        ):
            value = getattr(self, name)
            if not Decimal("0") < value <= Decimal("1"):
                raise InvariantViolationError(f"{name} 必须在 (0, 1] 区间，收到 {value}")


@dataclass
class Portfolio:
    """组合。"""

    equity: Money
    peak_equity: Money
    constraints: PortfolioConstraints = field(default_factory=PortfolioConstraints)
    instances: dict[str, StrategyInstance] = field(default_factory=dict)

    def register(self, instance: StrategyInstance) -> None:
        if instance.instance_id in self.instances:
            raise InvariantViolationError(f"实例 {instance.instance_id} 已注册")
        self.instances[instance.instance_id] = instance

    def unregister(self, instance_id: str) -> None:
        self.instances.pop(instance_id, None)

    # ---- 敞口计算 ----

    def _symbol_positions(self, symbol: str) -> list[tuple[StrategyInstance, Position]]:
        result: list[tuple[StrategyInstance, Position]] = []
        for instance in self.instances.values():
            for position in instance.open_positions():
                if is_contract_of(symbol, position.contract):
                    result.append((instance, position))
        return result

    def instrument_exposure(self, symbol: str, contract_prices: Mapping[str, Price]) -> Money:
        """单品种绝对敞口金额（多空绝对值之和，按合约现价计）。"""
        total = Decimal("0")
        for _, position in self._symbol_positions(symbol):
            price = Decimal(contract_prices.get(position.contract, Decimal("0")))
            total += price * position.quantity * position.multiplier
        return Money(total)

    def net_direction(self, symbol: str, contract_prices: Mapping[str, Price]) -> Direction | None:
        """单品种净方向：None 表示无持仓或多空均衡。"""
        long_notional = Decimal("0")
        short_notional = Decimal("0")
        for _, position in self._symbol_positions(symbol):
            price = Decimal(contract_prices.get(position.contract, Decimal("0")))
            notional = price * position.quantity * position.multiplier
            if position.direction is Direction.LONG:
                long_notional += notional
            else:
                short_notional += notional
        if long_notional == short_notional:
            return None
        return Direction.LONG if long_notional > short_notional else Direction.SHORT

    def same_direction_exposure_pct(self, contract_prices: Mapping[str, Price]) -> Decimal:
        """全部实例同向合计敞口占组合权益比例（取多空较大侧）。"""
        if Decimal(self.equity) == ZERO:
            return Decimal("0")
        long_notional = Decimal("0")
        short_notional = Decimal("0")
        for instance in self.instances.values():
            for position in instance.open_positions():
                price = Decimal(contract_prices.get(position.contract, Decimal("0")))
                notional = price * position.quantity * position.multiplier
                if position.direction is Direction.LONG:
                    long_notional += notional
                else:
                    short_notional += notional
        gross = max(long_notional, short_notional)
        ratio = gross / Decimal(self.equity)
        return ratio if ratio > ZERO else ZERO

    # ---- 回撤 ----

    def total_drawdown(self) -> Decimal:
        """组合回撤比例（0~1）。"""
        peak = Decimal(self.peak_equity)
        if peak <= ZERO:
            return ZERO
        drawdown = (peak - Decimal(self.equity)) / peak
        return drawdown if drawdown > ZERO else ZERO

    def update_peak(self) -> None:
        if Decimal(self.equity) > Decimal(self.peak_equity):
            self.peak_equity = self.equity

    def instance_drawdown(self, instance_id: str) -> Decimal:
        """实例回撤比例（按切片口径）。"""
        instance = self.instances.get(instance_id)
        if instance is None:
            raise InvariantViolationError(f"实例 {instance_id} 未注册")
        peak = Decimal(instance.slice_peak)
        if peak <= ZERO:
            return ZERO
        current = peak + Decimal(instance.daily_pnl)  # 简化：切片当前权益 = 峰值 + 当日盈亏
        drawdown = (peak - current) / peak
        return drawdown if drawdown > ZERO else ZERO

    def total_allocated(self) -> Money:
        return Money(sum((Decimal(i.capital_slice) for i in self.instances.values()), ZERO))

    def allocation_ratio(self) -> Decimal:
        if Decimal(self.equity) == ZERO:
            return ZERO
        ratio = Decimal(self.total_allocated()) / Decimal(self.equity)
        return ratio if ratio > ZERO else ZERO
