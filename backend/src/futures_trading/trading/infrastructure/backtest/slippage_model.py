"""SlippageModel：成交假设集中管理。

所有滑点/手续费假设只在这里建模一次，回测与实盘校准共用
（设计文档 7.2：撮合细节修正只改 adapter，不改 domain）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ....common.types import ZERO, Money, Price
from ...domain.model.signal import Direction


@dataclass(frozen=True)
class SlippageModel:
    """滑点与手续费模型。

    - ticks：市价单滑点 = N 个最小变动价位（不利方向）
    - commission_per_lot：每手手续费（开仓+平仓分别收取）
    """

    ticks: Decimal = Decimal("1")
    commission_per_lot: Money = Money(Decimal("10"))

    def execution_price(self, intended: Price, direction: Direction, tick_size: Decimal) -> Price:
        """市价单成交价：买入加价、卖出减价（不利方向）。"""
        if tick_size <= ZERO:
            raise ValueError("tick_size 必须为正")
        delta = self.ticks * tick_size
        px = Decimal(intended) + delta if direction is Direction.LONG else Decimal(intended) - delta
        return Price(px)

    def commission(self, lots: int) -> Money:
        if lots < 0:
            raise ValueError("lots 不能为负")
        return Money(self.commission_per_lot * lots)
