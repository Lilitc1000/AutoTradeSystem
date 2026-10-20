"""M3-e3 测试：手动下单 API（校验 + 投递 trade.order）。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from futures_trading.trading.router import manual_order as manual_module

pytestmark = pytest.mark.asyncio


class FakeBus:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish_order_command(self, payload: dict[str, Any]) -> None:
        self.published.append(payload)

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_bus(monkeypatch: pytest.MonkeyPatch) -> FakeBus:
    bus = FakeBus()
    monkeypatch.setattr(manual_module, "MessageBus", lambda *_a, **_k: bus)
    return bus


async def test_place_order_publishes_command(fake_bus: FakeBus) -> None:
    result = await manual_module.place_manual_order(
        manual_module.ManualOrderIn(
            contract="rb2701", direction="LONG", offset="OPEN", quantity=2, limit_price=Decimal("3067")
        )
    )
    assert result["accepted"] is True
    assert result["symbol"] == "rb"
    assert fake_bus.published[0]["contract"] == "rb2701"
    assert fake_bus.published[0]["quantity"] == "2"
    assert fake_bus.published[0]["limit_price"] == "3067"
    assert fake_bus.published[0]["instance_id"] == "manual"


async def test_close_today_passes_through(fake_bus: FakeBus) -> None:
    """SHFE 平今标志必须透传到出口（M2 平仓拆分的延续）。"""
    await manual_module.place_manual_order(
        manual_module.ManualOrderIn(
            contract="rb2701", direction="SHORT", offset="CLOSE_TODAY", quantity=1,
            limit_price=Decimal("3067"),
        )
    )
    assert fake_bus.published[0]["offset"] == "CLOSE_TODAY"


async def test_unknown_contract_rejected(fake_bus: FakeBus) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="未知合约"):
        await manual_module.place_manual_order(
            manual_module.ManualOrderIn(contract="zz9999", direction="LONG", quantity=1, limit_price=Decimal("1"))
        )
    assert fake_bus.published == []


async def test_limit_without_price_rejected(fake_bus: FakeBus) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="限价单必须提供"):
        await manual_module.place_manual_order(
            manual_module.ManualOrderIn(contract="rb2701", direction="LONG", quantity=1)
        )
    assert fake_bus.published == []


async def test_market_with_price_rejected(fake_bus: FakeBus) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="市价单不带限价"):
        await manual_module.place_manual_order(
            manual_module.ManualOrderIn(
                contract="rb2701", direction="LONG", quantity=1,
                price_type="MARKET", limit_price=Decimal("3067"),
            )
        )
    assert fake_bus.published == []
