"""执行器：把"目标数量"变成真实成交（方案②追单循环）。

背景（为什么需要这层）：
- 形态型策略只关心"到目标仓位"，不关心单点价格；
- 但限价单在盘口深度不足时只会部分成交（用户场景：要 10 手，对手盘仅 2 手），
  剩余量永远挂在队列里等不到；
- SHFE（上期所）不支持市价单（错误码 79「不被支持的报单类型」，
  夜盘联调 21:19 实锤），所以"市价"的正确实现是**对手价 + N tick 的限价单**。

解法：对手价 ± N tick 发限价单 → 未成交部分撤单 → 以更激进价格重发 →
循环直到全部成交 / 达到最大次数 / 触及滑点上限（宁可少开不多亏）。

分层原则：策略层只输出目标 delta（+10 手），执行层负责订单生命周期；
OrderPort 接口不变，执行器是它的上层使用者。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from ...common.types import ZERO, Price, Quantity
from ..domain.model.instrument import Instrument
from ..domain.model.order import Order
from ..domain.model.signal import Direction
from .dtos import OrderCommand, Tick
from .ports import OrderPort

__all__ = ["ExecutionConfig", "ExecutionResult", "ExecutionService"]


@dataclass(frozen=True)
class ExecutionConfig:
    """追单执行参数。"""

    tick_offset: int = 2  # 首轮相对对手价的偏移（tick 数）
    chase_step_ticks: int = 2  # 每轮追加的偏移（越追越激进）
    timeout_seconds: float = 3.0  # 单轮等待成交的时间
    poll_interval: float = 0.2  # 轮询订单状态间隔
    max_attempts: int = 4  # 最大轮次（含首轮）
    max_slippage_ticks: int = 12  # 累计滑点上限（相对首轮参考价）

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts 至少为 1")
        if self.tick_offset < 0 or self.chase_step_ticks < 0:
            raise ValueError("tick 偏移不能为负")


@dataclass(frozen=True)
class ExecutionResult:
    """执行结果。"""

    requested: Quantity
    filled: Quantity
    remaining: Quantity
    attempts: int
    avg_price: Price | None
    completed: bool

    @property
    def fill_ratio(self) -> Decimal:
        """成交比例（0~1）。"""
        if Decimal(self.requested) == ZERO:
            return ZERO
        return Decimal(self.filled) / Decimal(self.requested)


class ExecutionService:
    """追单执行器。

    tick_provider：返回某合约最新行情（Tick）的可调用对象；无行情时返回 None
    （此时回落用 last_price 或跳过执行——由调用方决定）。
    """

    def __init__(
        self,
        order_port: OrderPort,
        tick_provider: Callable[[str], Tick | None],
        instruments: dict[str, Instrument],
        config: ExecutionConfig | None = None,
    ) -> None:
        self.orders = order_port
        self.ticks = tick_provider
        self.instruments = instruments
        self.config = config or ExecutionConfig()

    # ---- 价格计算 ----

    def _tick_size(self, symbol: str) -> Decimal:
        instrument = self.instruments.get(symbol)
        return Decimal(instrument.tick_size) if instrument is not None else Decimal("1")

    def _reference_price(self, direction: Direction, tick: Tick | None) -> Decimal | None:
        """参考价：买入取卖一（对手价），卖出取买一；缺失回落最新价。"""
        if tick is None:
            return None
        if direction is Direction.LONG:
            return Decimal(tick.ask_price) if tick.ask_price is not None else Decimal(tick.last_price)
        return Decimal(tick.bid_price) if tick.bid_price is not None else Decimal(tick.last_price)

    def _chase_price(self, direction: Direction, reference: Decimal, attempt: int, symbol: str) -> Decimal:
        """第 attempt 轮的限价：对手价 ± (offset + (attempt-1)*step) 个 tick。"""
        step = self.config.tick_offset + (attempt - 1) * self.config.chase_step_ticks
        delta = self._tick_size(symbol) * step
        # 买入向上追（更容易成交），卖出向下追
        return reference + delta if direction is Direction.LONG else reference - delta

    def _slippage_exceeded(self, direction: Direction, reference: Decimal, price: Decimal, symbol: str) -> bool:
        """滑点保护：相对参考价的偏移超过上限则停止追单。"""
        limit = self._tick_size(symbol) * self.config.max_slippage_ticks
        diff = price - reference if direction is Direction.LONG else reference - price
        return diff > limit

    # ---- 执行主循环 ----

    async def execute(self, command: OrderCommand, tick: Tick | None = None) -> ExecutionResult:
        """执行指令，返回成交结果（尽量把 requested 全部成交）。"""
        if tick is None:
            tick = self.ticks(command.contract)
        reference = self._reference_price(command.direction, tick)
        if reference is None:
            # 无行情：无法定价，直接按原指令提交一次（限价/市价由指令决定）
            return await self._submit_once(command)

        requested = Decimal(command.quantity)
        remaining = requested
        filled_total = Decimal("0")
        notional = Decimal("0")
        attempts = 0

        for attempt in range(1, self.config.max_attempts + 1):
            if remaining <= ZERO:
                break
            price = self._chase_price(command.direction, reference, attempt, command.symbol)
            if self._slippage_exceeded(command.direction, reference, price, command.symbol):
                break  # 滑点保护：宁可少开不多亏
            attempts = attempt
            round_command = replace(
                command,
                quantity=Quantity(remaining),
                price_type=command.price_type,
                limit_price=Price(price),
            )
            order = self.orders.submit(round_command)
            filled = await self._wait_fill(order, self.config.timeout_seconds)
            filled_total += filled
            remaining -= filled
            if filled > ZERO and order.avg_fill_price is not None:
                notional += filled * Decimal(order.avg_fill_price)
            if remaining > ZERO:
                # 未成交部分撤单，下一轮以更激进价格重发
                self.orders.cancel(order.order_id)

        avg = notional / filled_total if filled_total > ZERO else None
        return ExecutionResult(
            requested=Quantity(requested),
            filled=Quantity(filled_total),
            remaining=Quantity(max(remaining, ZERO)),
            attempts=attempts,
            avg_price=Price(avg) if avg is not None else None,
            completed=remaining <= ZERO,
        )

    async def _submit_once(self, command: OrderCommand) -> ExecutionResult:
        """无行情回落：原样提交一次并等待成交。"""
        order = self.orders.submit(command)
        filled = await self._wait_fill(order, self.config.timeout_seconds)
        remaining = Decimal(command.quantity) - filled
        avg = Decimal(order.avg_fill_price) if (filled > ZERO and order.avg_fill_price) else None
        return ExecutionResult(
            requested=command.quantity,
            filled=Quantity(filled),
            remaining=Quantity(max(remaining, ZERO)),
            attempts=1,
            avg_price=Price(avg) if avg is not None else None,
            completed=remaining <= ZERO,
        )

    async def _wait_fill(self, order: Order, timeout: float) -> Decimal:
        """轮询订单成交数量（OrderPort.query），直到全部成交/终态/超时。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last = Decimal(order.filled_quantity)
        while True:
            current = self.orders.query(order.order_id)
            if current is not None:
                last = Decimal(current.filled_quantity)
                if current.is_final:
                    return last
            if loop.time() >= deadline:
                return last
            await asyncio.sleep(self.config.poll_interval)
