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
    """持仓：同向持仓数量 + 加权均价。

    today_quantity：**今仓**（当日开仓部分）——SHFE 平仓必须区分平今/平昨
    （错误混用被柜台拒），昨仓 = quantity - today_quantity。
    跨日由日切逻辑处理（今仓转昨仓：today_quantity 归零）。
    """

    instance_id: str
    contract: str
    direction: Direction
    multiplier: Decimal  # 合约乘数（盈亏换算需要）
    quantity: Quantity = Quantity(ZERO)
    avg_price: Price | None = None
    today_quantity: Quantity = Quantity(ZERO)

    def __post_init__(self) -> None:
        if self.multiplier <= Decimal("0"):
            raise ValueError("multiplier 必须为正")

    @property
    def yesterday_quantity(self) -> Quantity:
        """昨仓 = 总量 - 今仓。"""
        return Quantity(max(self.quantity - self.today_quantity, ZERO))

    def roll_new_year(self) -> None:
        """日切：今仓全部转昨仓。"""
        self.today_quantity = Quantity(ZERO)

    def add(self, fill: Fill) -> None:
        """同向开仓：数量累加，均价加权（新开仓计入今仓）。"""
        qty = Decimal(fill.quantity)
        if qty <= ZERO:
            raise InvalidFillError(f"开仓量必须为正，收到 {fill.quantity}")
        if self.avg_price is None or self.quantity == ZERO:
            self.avg_price = Price(Decimal(fill.price))
            self.quantity = Quantity(qty)
            self.today_quantity = Quantity(qty)
            return
        total = self.quantity * Decimal(self.avg_price) + qty * Decimal(fill.price)
        self.quantity = Quantity(self.quantity + qty)
        self.avg_price = Price(total / self.quantity)
        self.today_quantity = Quantity(self.today_quantity + qty)

    def reduce(self, fill: Fill, *, close_today: bool = False) -> Money:
        """平仓：扣减数量，返回已实现盈亏。

        close_today=True 走平今（扣今仓），False 走平昨（扣昨仓）——
        SHFE 语义；调用方（signal_pipeline）按持仓今昨构成选路。
        平仓价与开仓均价之差 × 手数 × 乘数，按方向定号。
        """
        qty = Decimal(fill.quantity)
        if qty <= ZERO:
            raise InvalidFillError(f"平仓量必须为正，收到 {fill.quantity}")
        if qty > self.quantity:
            raise InsufficientPositionError(
                f"平仓量 {qty} 超过持仓 {self.quantity}（contract={self.contract}）"
            )
        if close_today and qty > self.today_quantity:
            raise InsufficientPositionError(
                f"平今量 {qty} 超过今仓 {self.today_quantity}（contract={self.contract}）"
            )
        if self.avg_price is None:
            raise InsufficientPositionError("持仓均价为空，无法平仓")
        entry = Decimal(self.avg_price)
        exit_price = Decimal(fill.price)
        price_diff = exit_price - entry if self.direction is Direction.LONG else entry - exit_price
        realized = Money(price_diff * qty * self.multiplier)
        self.quantity = Quantity(self.quantity - qty)
        if close_today:
            self.today_quantity = Quantity(self.today_quantity - qty)
        if self.quantity == ZERO:
            self.avg_price = None
            self.today_quantity = Quantity(ZERO)
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
