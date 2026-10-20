"""总线报单端口（M3-e2b）：把指令发往 Redis `trade.order`。

为什么需要它：strategy_core 此前自持 CtpTradeGateway 直连 CTP——与
trade_gateway（M3-e2 交易出口）同时运行会撞 SimNow 并发连接限制，且
策略/手动两条执行链路分裂。改为发总线后：
- 策略信号与前端手动单走**同一出口**（同一追单/滑点/风控链路）
- 策略进程不占 CTP 连接，崩溃不影响交易出口（7×24 分层）

同步接口（submit）供 SignalPipeline 使用——实现 OrderPort 鸭子类型；
入队的指令由 asyncio 主循环 drain 并发布。
"""

from __future__ import annotations

from collections import deque

from ....common.types import Price, Quantity
from ...application.dtos import OrderCommand
from ...domain.model.order import Order


class BusOrderPort:
    """指令只入队（同步），发布由主循环 drain 后异步执行。"""

    def __init__(self) -> None:
        self._pending: deque[OrderCommand] = deque()
        self._seq = 0

    def submit(self, command: OrderCommand) -> Order:
        """入队并返回一个待发订单（SignalOutcome 需要 order_id）。

        注意：真实柜台 order_id 由 trade_gateway 侧生成并通过
        trade.fill / trade.order.status 回传——这里的 order_id 是本地
        观测用（BUS- 前缀），不用于柜台查询。
        """
        self._seq += 1
        order = Order(
            order_id=f"BUS-{self._seq:06d}",
            idempotency_key=command.idempotency_key,
            instance_id=command.instance_id,
            contract=command.contract,
            symbol=command.symbol,
            direction=command.direction,
            offset=command.offset,
            quantity=Quantity(command.quantity),
            price_type=command.price_type,
            limit_price=Price(command.limit_price) if command.limit_price is not None else None,
        )
        order.submit()  # PENDING → SUBMITTED（已交出口）
        self._pending.append(command)
        return order

    def drain(self) -> list[OrderCommand]:
        """取走全部待发指令（主循环调用）。"""
        batch = list(self._pending)
        self._pending.clear()
        return batch

    def cancel(self, order_id: str) -> Order:
        raise NotImplementedError("撤单走 trade_gateway（指令侧不持订单状态）")

    def query(self, order_id: str) -> Order | None:  # noqa: ARG002 - 协议签名要求
        """本地只留待发订单——下单后状态在 trade_gateway 侧。"""
        return None

    @property
    def pending_count(self) -> int:
        return len(self._pending)


def command_to_payload(command: OrderCommand) -> dict[str, object]:
    """OrderCommand → 总线 JSON（trade.order 频道协议）。"""
    return {
        "instance_id": command.instance_id,
        "contract": command.contract,
        "symbol": command.symbol,
        "direction": str(command.direction),
        "offset": str(command.offset),
        "quantity": str(command.quantity),
        "price_type": str(command.price_type),
        "limit_price": str(command.limit_price) if command.limit_price is not None else None,
        "signal_id": command.signal_id,
    }
