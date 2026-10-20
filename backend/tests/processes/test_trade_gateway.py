"""M3-e2 测试：指令解析 + 订阅执行链路 + dry-run（Fake 总线，不连 CTP）。"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from futures_trading.processes.trade_gateway import _consume_orders, _payload_to_command
from futures_trading.trading.application.dtos import OrderCommand
from futures_trading.trading.domain.model.order import Direction, Offset, PriceType

pytestmark = pytest.mark.asyncio

PAYLOAD: dict[str, object] = {
    "instance_id": "i1",
    "contract": "rb2701",
    "symbol": "rb",
    "direction": "SHORT",
    "offset": "CLOSE_TODAY",
    "quantity": "3",
    "price_type": "LIMIT",
    "limit_price": "3067",
    "signal_id": "s-1",
}


class FakeBus:
    """最小总线替身：可注入订阅消息 + 收集发布。"""

    def __init__(self, messages: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        self._messages = messages or []
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def subscribe(self, *channels: str):  # noqa: ARG002 - 对齐真 bus 签名
        for channel, payload in self._messages:
            yield channel, payload
        # 让 gather 能正常退出（不再有新消息）
        await asyncio.sleep(0)

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        self.published.append((channel, payload))


class FakeExecutor:
    def __init__(self, *, completed: bool = True, raise_error: bool = False) -> None:
        self._completed = completed
        self._raise = raise_error
        self.calls: list[OrderCommand] = []

    async def execute(self, command: OrderCommand) -> Any:
        self.calls.append(command)
        if self._raise:
            raise RuntimeError("boom")
        from futures_trading.trading.application.execution import ExecutionResult

        return ExecutionResult(
            requested=command.quantity,
            filled=command.quantity if self._completed else 1,
            remaining=(0 if self._completed else command.quantity - 1),
            attempts=1,
            avg_price=Decimal("3067"),
            completed=self._completed,
        )


def test_payload_to_command_roundtrip() -> None:
    command = _payload_to_command(PAYLOAD)
    assert command.contract == "rb2701"
    assert command.symbol == "rb"
    assert command.direction is Direction.SHORT
    assert command.offset is Offset.CLOSE_TODAY
    assert command.quantity == 3
    assert command.price_type is PriceType.LIMIT
    assert command.limit_price == Decimal("3067")
    assert command.signal_id == "s-1"


def test_payload_defaults_and_symbol_fallback() -> None:
    command = _payload_to_command({"contract": "rb2701", "quantity": 1})
    assert command.symbol == "rb"  # 未给 symbol 时从合约推导
    assert command.direction is Direction.LONG
    assert command.offset is Offset.OPEN
    assert command.price_type is PriceType.LIMIT
    assert command.limit_price is None


async def test_consume_orders_publishes_status() -> None:
    executor = FakeExecutor()
    bus = FakeBus([("trade.order", PAYLOAD)])
    logger = __import__("logging").getLogger("test")
    await _consume_orders(executor, bus, logger)
    assert len(executor.calls) == 1
    assert executor.calls[0].contract == "rb2701"
    channel, payload = bus.published[0]
    assert channel == "trade.order.status"
    assert payload["status"] == "FILLED"
    assert payload["filled"] == "3"


async def test_partial_fill_status() -> None:
    executor = FakeExecutor(completed=False)
    bus = FakeBus([("trade.order", PAYLOAD)])
    await _consume_orders(executor, bus, __import__("logging").getLogger("test"))
    assert bus.published[0][1]["status"] == "PARTIAL"
    assert bus.published[0][1]["remaining"] == "2"


async def test_execution_error_publishes_error_status() -> None:
    executor = FakeExecutor(raise_error=True)
    bus = FakeBus([("trade.order", PAYLOAD)])
    await _consume_orders(executor, bus, __import__("logging").getLogger("test"))
    assert bus.published[0][1]["status"] == "ERROR"
    assert "boom" in str(bus.published[0][1]["msg"])


async def test_bad_payload_is_dropped() -> None:
    """非法方向：解析失败应丢弃并继续（不拖垮出口）。"""
    executor = FakeExecutor()
    bad = {**PAYLOAD, "direction": "SIDEWAYS"}
    bus = FakeBus([("trade.order", bad)])
    await _consume_orders(executor, bus, __import__("logging").getLogger("test"))
    assert executor.calls == []  # 未被下发
    assert bus.published == []


async def test_dry_run_does_not_execute() -> None:
    """dry-run：只解析并打印，不调用执行器（不占 CTP 连接）。"""
    bus = FakeBus([("trade.order", PAYLOAD)])
    await _consume_orders(None, bus, __import__("logging").getLogger("test"), dry_run=True)
    assert bus.published == []
