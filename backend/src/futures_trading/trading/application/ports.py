"""端口定义：domain 与外部世界的唯一契约。

- MarketDataPort：行情来源（回测：历史重放；实盘：CTP 行情）
- OrderPort：报单执行（回测：BacktestBroker；实盘：CTP 报单）
- AccountPort：账户查询（回测：虚拟账户；实盘：CTP 查询）
- ClockPort：时钟（回测：虚拟时钟，防未来函数；实盘：系统时钟）
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from ...common.types import Money
from ..domain.model.order import Order
from ..domain.model.position import Position
from .dtos import Bar, OrderCommand


@runtime_checkable
class MarketDataPort(Protocol):
    """行情端口。"""

    def subscribe(self, symbols: Sequence[str]) -> None:
        """订阅品种行情。"""
        ...

    def latest_bar(self, symbol: str, interval_minutes: int) -> Bar | None:
        """取最近一根 K 线；无数据返回 None。"""
        ...


@runtime_checkable
class OrderPort(Protocol):
    """报单端口。"""

    def submit(self, command: OrderCommand) -> Order:
        """提交指令，返回 PENDING 状态的订单。"""
        ...

    def cancel(self, order_id: str) -> Order:
        """撤销订单。"""
        ...

    def query(self, order_id: str) -> Order | None:
        """查询订单；不存在返回 None。"""
        ...


@runtime_checkable
class AccountPort(Protocol):
    """账户端口。"""

    def balance(self) -> Money:
        """可用资金。"""
        ...

    def positions(self) -> Sequence[Position]:
        """当前持仓。"""
        ...


@runtime_checkable
class ClockPort(Protocol):
    """时钟端口。"""

    def now(self) -> datetime:
        """当前时间。"""
        ...
