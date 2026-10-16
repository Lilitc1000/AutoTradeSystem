"""Position 实体：实例 × 合约的持仓。

不变量：持仓只能由成交驱动变化（设计文档建模红线），
禁止绕过 apply_fill / add / reduce 直接改写数量。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ....common.types import ZERO, Money, Price, Quantity
from ..errors import InsufficientPositionError, InvalidFillError
from .signal import Direction


@dataclass(frozen=True)
class Fill:
    """一笔成交（值对象）。"""

    quantity: Quantity
    price: Price


@dataclass
class Position:
    """持仓：同向持仓数量 + 加权均价。"""

    instance_id: str
    contract: str
    direction: Direction
    multiplier: Decimal  # 合约乘数（盈亏换算需要）
    quantity: Quantity = Quantity(ZERO)
    avg_price: Price | None = None

    def __post_init__(self) -> None:
        if self.multiplier <= Decimal("0"):
            raise ValueError("multiplier 必须为正")

    def add(self, fill: Fill) -> None:
        """同向开仓：数量累加，均价加权。"""
        qty = Decimal(fill.quantity)
        if qty <= ZERO:
            raise InvalidFillError(f"开仓量必须为正，收到 {fill.quantity}")
        if self.avg_price is None or self.quantity == ZERO:
            self.avg_price = Price(Decimal(fill.price))
            self.quantity = Quantity(qty)
            return
        total = self.quantity * Decimal(self.avg_price) + qty * Decimal(fill.price)
        self.quantity = Quantity(self.quantity + qty)
        self.avg_price = Price(total / self.quantity)

    def reduce(self, fill: Fill) -> Money:
        """平仓：扣减数量，返回已实现盈亏。

        平仓价与开仓均价之差 × 手数 × 乘数，按方向定号。
        """
        qty = Decimal(fill.quantity)
        if qty <= ZERO:
            raise InvalidFillError(f"平仓量必须为正，收到 {fill.quantity}")
        if qty > self.quantity:
            raise InsufficientPositionError(
                f"平仓量 {qty} 超过持仓 {self.quantity}（contract={self.contract}）"
            )
        if self.avg_price is None:
            raise InsufficientPositionError("持仓均价为空，无法平仓")
        entry = Decimal(self.avg_price)
        exit_price = Decimal(fill.price)
        price_diff = exit_price - entry if self.direction is Direction.LONG else entry - exit_price
        realized = Money(price_diff * qty * self.multiplier)
        self.quantity = Quantity(self.quantity - qty)
        if self.quantity == ZERO:
            self.avg_price = None
        return realized

    def unrealized_pnl(self, last_price: Price) -> Money:
        """浮动盈亏。"""
        if self.avg_price is None or self.quantity == ZERO:
            return Money(ZERO)
        price_diff = Decimal(last_price) - Decimal(self.avg_price)
        if self.direction is Direction.SHORT:
            price_diff = -price_diff
        return Money(price_diff * self.quantity * self.multiplier)

    def is_flat(self) -> bool:
        return bool(Decimal(self.quantity) == ZERO)
