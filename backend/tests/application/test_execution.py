"""执行器单测：追单循环（方案②）。

覆盖用户核心场景：要 10 手但盘口只有 2 手 → 部分成交 → 撤单 → 更激进价格重发。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from futures_trading.common.types import Price, Quantity
from futures_trading.trading.application.dtos import OrderCommand, Tick
from futures_trading.trading.application.execution import ExecutionConfig, ExecutionService
from futures_trading.trading.domain.model.instrument import ContractStatus, Exchange, Instrument
from futures_trading.trading.domain.model.order import Offset, Order, PriceType
from futures_trading.trading.domain.model.signal import Direction

NOW = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


def _instrument(symbol: str = "rb") -> Instrument:
    return Instrument(
        symbol=symbol,
        contract=f"{symbol}2701",
        name="螺纹钢",
        exchange=Exchange.SHFE,
        multiplier=Decimal("10"),
        tick_size=Decimal("1"),
        margin_rate=Decimal("0.1"),
        status=ContractStatus.ACTIVE,
    )


def _tick(ask: str = "3084", bid: str = "3082", last: str = "3083") -> Tick:
    return Tick(
        symbol="rb",
        ts=NOW,
        last_price=Price(Decimal(last)),
        bid_price=Price(Decimal(bid)),
        ask_price=Price(Decimal(ask)),
    )


def _command(quantity: str = "10", price_type: PriceType = PriceType.LIMIT) -> OrderCommand:
    return OrderCommand(
        instance_id="i1",
        contract="rb2701",
        symbol="rb",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        quantity=Quantity(Decimal(quantity)),
        price_type=price_type,
        limit_price=Price(Decimal("3084")),
        signal_id="s1",
    )


class FakeOrderPort:
    """按 fill_plan 模拟每轮成交数量（None=全成交）。"""

    def __init__(self, fill_plan: list[str] | None = None) -> None:
        self.fill_plan = fill_plan
        self.submitted: list[OrderCommand] = []
        self.cancelled: list[str] = []
        self.orders: dict[str, Order] = {}
        self._seq = 0

    def submit(self, command: OrderCommand) -> Order:
        self._seq += 1
        order_id = f"o{self._seq}"
        order = Order(
            order_id=order_id,
            idempotency_key=command.idempotency_key,
            instance_id=command.instance_id,
            contract=command.contract,
            symbol=command.symbol,
            direction=command.direction,
            offset=command.offset,
            quantity=command.quantity,
            price_type=command.price_type,
            limit_price=Decimal(command.limit_price or 0),
            signal_id=command.signal_id,
        )
        order.submit()
        round_index = len(self.submitted)
        planned = (
            Decimal(self.fill_plan[min(round_index, len(self.fill_plan) - 1)])
            if self.fill_plan
            else Decimal(command.quantity)
        )
        filled = min(planned, Decimal(command.quantity))
        if filled > 0:
            order.partial_fill(filled, Decimal(command.limit_price or 0))
        self.orders[order_id] = order
        self.submitted.append(command)
        return order

    def cancel(self, order_id: str) -> Order:
        order = self.orders[order_id]
        if not order.is_final:
            order.cancel()
        self.cancelled.append(order_id)
        return order

    def query(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)


def _service(port: FakeOrderPort, config: ExecutionConfig | None = None) -> ExecutionService:
    return ExecutionService(
        order_port=port,
        tick_provider=lambda _contract: _tick(),
        instruments={"rb": _instrument()},
        config=config,
    )


class TestExecute:
    @pytest.mark.asyncio
    async def test_full_fill(self) -> None:
        port = FakeOrderPort(fill_plan=["10"])
        result = await _service(port).execute(_command())
        assert result.filled == Quantity(Decimal("10"))
        assert result.completed is True
        assert result.attempts == 1
        assert not port.cancelled  # 一次成交完，无需撤单

    @pytest.mark.asyncio
    async def test_partial_then_chase(self) -> None:
        """用户场景：10 手但盘口只吃下 2 手 → 撤单后追价补 8 手。"""
        port = FakeOrderPort(fill_plan=["2", "8"])
        result = await _service(port).execute(_command())
        assert result.filled == Quantity(Decimal("10"))
        assert result.completed is True
        assert result.attempts == 2
        assert len(port.cancelled) == 1  # 第一轮未成交部分被撤
        # 追价：第二轮限价必须比第一轮更激进（买入向上）
        first_price = Decimal(port.submitted[0].limit_price or 0)
        second_price = Decimal(port.submitted[1].limit_price or 0)
        assert second_price > first_price
        # 首轮 = 卖一(3084) + 2 tick = 3086
        assert first_price == Decimal("3086")

    @pytest.mark.asyncio
    async def test_give_up_after_max_attempts(self) -> None:
        port = FakeOrderPort(fill_plan=["1", "1", "1", "1"])
        config = ExecutionConfig(max_attempts=3, timeout_seconds=0.1)
        result = await _service(port, config).execute(_command("10"))
        assert result.attempts == 3
        assert result.filled == Quantity(Decimal("3"))
        assert result.remaining == Quantity(Decimal("7"))
        assert result.completed is False
        assert len(port.cancelled) == 3

    @pytest.mark.asyncio
    async def test_slippage_guard_stops_chasing(self) -> None:
        """滑点保护：上限设死，第二轮价格超限则立即停止（宁可少开不多亏）。"""
        port = FakeOrderPort(fill_plan=["1"])
        config = ExecutionConfig(tick_offset=2, chase_step_ticks=10, max_slippage_ticks=3, timeout_seconds=0.1)
        result = await _service(port, config).execute(_command("10"))
        # 第二轮偏移 12 tick > 上限 3 tick → 不再追
        assert result.attempts == 1
        assert result.completed is False

    @pytest.mark.asyncio
    async def test_no_tick_falls_back_to_single_submit(self) -> None:
        """无行情：无法定价，原样提交一次。"""
        port = FakeOrderPort()
        service = ExecutionService(
            order_port=port,
            tick_provider=lambda _contract: None,
            instruments={"rb": _instrument()},
        )
        result = await service.execute(_command("5"))
        assert result.filled == Quantity(Decimal("5"))
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_sell_chases_downward(self) -> None:
        port = FakeOrderPort(fill_plan=["2", "8"])
        command = OrderCommand(
            instance_id="i1",
            contract="rb2701",
            symbol="rb",
            direction=Direction.SHORT,
            offset=Offset.CLOSE,
            quantity=Quantity(Decimal("10")),
            price_type=PriceType.LIMIT,
            limit_price=Price(Decimal("3082")),
            signal_id="s2",
        )
        result = await _service(port).execute(command)
        assert result.completed is True
        first = Decimal(port.submitted[0].limit_price or 0)
        second = Decimal(port.submitted[1].limit_price or 0)
        # 卖出向下追（更容易成交）
        assert second < first
        assert first == Decimal("3080")  # 买一(3082) - 2 tick


class TestConfig:
    def test_invalid_config_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            ExecutionConfig(max_attempts=0)
        with pytest.raises(ValueError, match="tick 偏移"):
            ExecutionConfig(tick_offset=-1)
