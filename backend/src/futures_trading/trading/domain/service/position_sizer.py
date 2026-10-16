"""PositionSizer 领域服务：ATR 风险预算法 + 四层取小。

设计文档 5.1/5.2：
  L1 单笔风险预算：lots_base = floor(risk_budget / (N × ATR × multiplier))
  L2 品种净敞口上限
  L3 保证金使用率上限
  L4 策略资金切片上限
  lots = min(L1..L4)；不足最小交易单位则放弃本次信号。

该服务是纯函数式（输入快照 → 输出结果），便于属性测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ....common.errors import ValidationError
from ....common.types import ZERO, Money, Price, Quantity, floor_to_int


@dataclass(frozen=True)
class SizingConstraints:
    """L2/L3/L4 三层组合约束的快照。"""

    instrument_exposure_cap: Money  # 品种敞口上限（金额）
    current_instrument_exposure: Money  # 该品种当前绝对敞口（金额）
    available_margin: Money  # 可用保证金
    margin_usage_cap_pct: Decimal  # 保证金使用率上限（0~1）
    strategy_slice_remaining: Money  # 策略切片剩余可用额度
    min_lots: int = 1


@dataclass(frozen=True)
class SizingInput:
    """仓位计算的完整输入。"""

    capital: Money  # 实例资金切片
    risk_per_trade_pct: Decimal  # 单笔风险比例（0~1）
    atr: Price  # ATR(20) 价差
    stop_multiplier: Decimal  # 止损倍数 N
    multiplier: Decimal  # 合约乘数
    tick_size: Decimal  # 最小变动价位
    price: Price  # 当前价（保证金换算）
    margin_rate: Decimal  # 保证金比例
    constraints: SizingConstraints

    def __post_init__(self) -> None:
        if Decimal(self.capital) <= ZERO:
            raise ValidationError("capital 必须为正")
        if not Decimal("0") < Decimal(self.risk_per_trade_pct) <= Decimal("1"):
            raise ValidationError("risk_per_trade_pct 必须在 (0, 1]")
        if Decimal(self.atr) <= ZERO:
            raise ValidationError("atr 必须为正")
        if Decimal(self.stop_multiplier) <= ZERO:
            raise ValidationError("stop_multiplier 必须为正")
        if Decimal(self.multiplier) <= ZERO:
            raise ValidationError("multiplier 必须为正")
        if Decimal(self.tick_size) <= ZERO:
            raise ValidationError("tick_size 必须为正")
        if Decimal(self.price) <= ZERO:
            raise ValidationError("price 必须为正")


@dataclass(frozen=True)
class SizingResult:
    """仓位计算结果。"""

    lots: int  # 最终手数（0 = 放弃本次信号）
    lots_by_layer: dict[str, int]  # 各层允许的最大手数
    binding_layer: str | None  # 起决定作用的层；None 表示 lots=0
    risk_budget: Money  # 单笔风险预算金额
    risk_per_lot: Money  # 每手 worst-case 亏损


def calculate(inp: SizingInput) -> SizingResult:
    """四层取小计算目标手数。"""
    risk_budget = Decimal(inp.capital) * Decimal(inp.risk_per_trade_pct)
    stop_distance = Decimal(inp.atr) * Decimal(inp.stop_multiplier)
    risk_per_lot = stop_distance * Decimal(inp.multiplier)

    lots_l1 = floor_to_int(risk_budget / risk_per_lot) if risk_per_lot > ZERO else 0

    margin_per_lot = Decimal(inp.price) * Decimal(inp.multiplier) * Decimal(inp.margin_rate)
    if margin_per_lot <= ZERO:
        raise ValidationError("每手保证金必须为正，请检查 price/multiplier/margin_rate")

    room_l2 = Decimal(inp.constraints.instrument_exposure_cap) - Decimal(
        inp.constraints.current_instrument_exposure
    )
    lots_l2 = floor_to_int(room_l2 / margin_per_lot) if room_l2 > ZERO else 0

    usable_margin = Decimal(inp.constraints.available_margin) * Decimal(inp.constraints.margin_usage_cap_pct)
    lots_l3 = floor_to_int(usable_margin / margin_per_lot) if usable_margin > ZERO else 0

    lots_l4 = (
        floor_to_int(Decimal(inp.constraints.strategy_slice_remaining) / margin_per_lot)
        if Decimal(inp.constraints.strategy_slice_remaining) > ZERO
        else 0
    )

    layers = {"L1": lots_l1, "L2": lots_l2, "L3": lots_l3, "L4": lots_l4}
    candidate = min(layers.values())
    if candidate < inp.constraints.min_lots:
        return SizingResult(
            lots=0,
            lots_by_layer=layers,
            binding_layer=None,
            risk_budget=Money(risk_budget),
            risk_per_lot=Money(risk_per_lot),
        )

    binding = min((name for name, value in layers.items() if value == candidate), key=lambda n: int(n[1]))
    return SizingResult(
        lots=candidate,
        lots_by_layer=layers,
        binding_layer=binding,
        risk_budget=Money(risk_budget),
        risk_per_lot=Money(risk_per_lot),
    )


__all__ = ["SizingConstraints", "SizingInput", "SizingResult", "calculate", "Quantity"]
