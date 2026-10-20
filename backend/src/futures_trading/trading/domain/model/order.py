"""Order 聚合根：订单全生命周期。

不变量：
- 状态迁移必须合法（显式状态机，设计文档 P4）
- filled_quantity <= quantity
- 幂等键唯一（生成规则见 make_idempotency_key）
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from ....common.types import ZERO, Quantity
from ..errors import InvalidOrderTransitionError
from .signal import Direction


class Offset(StrEnum):
    """开平标志。

    SHFE（上期所）特殊：平仓必须区分**平今**（'3'）与**平昨**（'1'）——
    混用/只用其一会被柜台拒单（"持仓不足"）。日内策略的平仓主体是平今，
    故枚举显式区分，由持仓今昨构成决定拆分（signal_pipeline._emit_close）。
    """

    OPEN = "OPEN"
    CLOSE = "CLOSE"  # 通用平仓（非 SHFE 交易所/不区分今昔的简化路径）
    CLOSE_TODAY = "CLOSE_TODAY"  # SHFE 平今
    CLOSE_YESTERDAY = "CLOSE_YESTERDAY"  # SHFE 平昨


class PriceType(StrEnum):
    """委托类型。"""

    LIMIT = "LIMIT"  # 限价单
    MARKET = "MARKET"  # 市价单


class OrderStatus(StrEnum):
    """订单状态。"""

    PENDING = "PENDING"  # 已生成，待送达柜台
    SUBMITTED = "SUBMITTED"  # 已送达柜台
    PARTIAL = "PARTIAL"  # 部分成交
    FILLED = "FILLED"  # 全部成交（终态）
    CANCELLED = "CANCELLED"  # 已撤销（终态）
    REJECTED = "REJECTED"  # 已拒绝（终态）


_LEGAL_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.SUBMITTED, OrderStatus.REJECTED, OrderStatus.CANCELLED}),
    OrderStatus.SUBMITTED: frozenset(
        {OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}
    ),
    OrderStatus.PARTIAL: frozenset({OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED}),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}


def make_idempotency_key(instance_id: str, signal_id: str, direction: Direction, offset: Offset, contract: str) -> str:
    """生成幂等键：同一信号的同一意图只允许一个订单。"""
    raw = "|".join([instance_id, signal_id, str(direction), str(offset), contract])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass
class Order:
    """订单聚合根。"""

    order_id: str
    idempotency_key: str
    instance_id: str
    contract: str
    symbol: str
    direction: Direction
    offset: Offset
    quantity: Quantity
    price_type: PriceType
    limit_price: Decimal | None = None
    signal_id: str | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Quantity = Quantity(ZERO)
    avg_fill_price: Decimal | None = None
    reject_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if Decimal(self.quantity) <= ZERO:
            raise ValueError(f"quantity 必须为正，收到 {self.quantity}")
        if self.price_type is PriceType.LIMIT and self.limit_price is None:
            raise ValueError("限价单必须提供 limit_price")

    # ---- 状态迁移（唯一允许修改 status 的入口）----

    def _transition(self, target: OrderStatus) -> None:
        if target not in _LEGAL_TRANSITIONS[self.status]:
            raise InvalidOrderTransitionError(f"非法订单状态迁移: {self.status.value} -> {target.value}")
        self.status = target
        self.updated_at = datetime.now(UTC)

    def submit(self) -> None:
        self._transition(OrderStatus.SUBMITTED)

    def cancel(self) -> None:
        self._transition(OrderStatus.CANCELLED)

    def reject(self, reason: str) -> None:
        self.reject_reason = reason
        self._transition(OrderStatus.REJECTED)

    # ---- 成交 ----

    def partial_fill(self, quantity: Quantity, price: Decimal) -> None:
        """记录一笔部分成交；数量累计，均价加权。"""
        qty = Decimal(quantity)
        px = Decimal(price)
        if qty <= ZERO:
            raise ValueError(f"成交量必须为正，收到 {quantity}")
        if self.filled_quantity + qty > self.quantity:
            raise ValueError(
                f"成交累计量 {self.filled_quantity + qty} 超出订单量 {self.quantity}"
            )
        if self.avg_fill_price is None or self.filled_quantity == ZERO:
            self.avg_fill_price = px
        else:
            total = self.filled_quantity * self.avg_fill_price + qty * px
            self.avg_fill_price = total / (self.filled_quantity + qty)
        self.filled_quantity = Quantity(self.filled_quantity + qty)
        if self.status is OrderStatus.PENDING:
            # 成交回报可能先于委托确认到达（CTP 回报乱序）——
            # 成交前必先提交，补一次合法迁移
            self._transition(OrderStatus.SUBMITTED)
        if self.filled_quantity == self.quantity:
            self._transition(OrderStatus.FILLED)
        else:
            self._transition(OrderStatus.PARTIAL)

    def fill(self, quantity: Quantity, price: Decimal) -> None:
        """便捷方法：成交指定数量（调用方保证不超量）。"""
        self.partial_fill(quantity, price)

    # ---- 查询 ----

    @property
    def remaining_quantity(self) -> Quantity:
        return Quantity(self.quantity - self.filled_quantity)

    @property
    def is_final(self) -> bool:
        return not _LEGAL_TRANSITIONS[self.status]
