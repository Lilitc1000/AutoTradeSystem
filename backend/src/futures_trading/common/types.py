"""基础价值类型与通用工具。

所有价格、数量、金额一律使用 Decimal，禁止 float：
金融计算中 float 的二进制表示误差会在累计/复利口径下放大。
"""

from __future__ import annotations

from decimal import Decimal
from typing import NewType

Price = NewType("Price", Decimal)
Quantity = NewType("Quantity", Decimal)  # 期货手数：整数语义，Decimal 承载
Money = NewType("Money", Decimal)

ZERO = Decimal("0")
ONE = Decimal("1")


def round_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    """按最小变动价位取整（四舍五入到 tick 的整数倍）。"""
    if tick_size <= ZERO:
        raise ValueError(f"tick_size 必须为正，收到 {tick_size}")
    ticks = (Decimal(price) / tick_size).quantize(Decimal("1"))
    return ticks * tick_size


def floor_to_int(value: Decimal) -> int:
    """向下取整为整数（手数计算专用，负值抛错）。"""
    if value < ZERO:
        raise ValueError(f"手数不能为负，收到 {value}")
    return int(value.to_integral_value(rounding="ROUND_FLOOR"))
