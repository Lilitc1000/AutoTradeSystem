"""用例值对象：行情 Bar 与下单指令 OrderCommand。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ...common.types import ZERO, Price, Quantity
from ..domain.model.order import Offset, PriceType, make_idempotency_key
from ..domain.model.signal import Direction


@dataclass(frozen=True)
class Bar:
    """一根 K 线（任意周期）。"""

    symbol: str
    interval_minutes: int
    ts: datetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Quantity
    open_interest: Quantity = Quantity(ZERO)

    def __post_init__(self) -> None:
        if self.interval_minutes <= 0:
            raise ValueError("interval_minutes 必须为正")
        if Decimal(self.high) < Decimal(self.low):
            raise ValueError("high 不能小于 low")


@dataclass(frozen=True)
class OrderCommand:
    """通过风控终检后的可执行指令（带幂等键）。"""

    instance_id: str
    contract: str
    symbol: str
    direction: Direction
    offset: Offset
    quantity: Quantity
    price_type: PriceType
    limit_price: Price | None = None
    signal_id: str | None = None

    @property
    def idempotency_key(self) -> str:
        signal_id = self.signal_id or ""
        return make_idempotency_key(self.instance_id, signal_id, self.direction, self.offset, self.contract)
