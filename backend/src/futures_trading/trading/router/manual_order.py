"""手动下单 API（M3-e3）：前端工作台 → Redis trade.order → trade_gateway。

风控口径（用户选定「人工优先」）：只过基础校验（合约注册/方向/开平/
手数/限价），**不过策略风控**（ATR 止损是策略逻辑，不该绑住人工——
紧急时人工要能自己决定）；全量记 audit_log。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...common.instrument_registry import get_instrument
from ...common.types import Price, Quantity
from ..application.dtos import OrderCommand
from ..domain.model.order import Direction, Offset, PriceType
from ..infrastructure.messaging.bus import MessageBus
from ..infrastructure.messaging.bus_order_port import command_to_payload

router = APIRouter(prefix="/api/trading", tags=["trading-manual"])


class ManualOrderIn(BaseModel):
    contract: str = Field(min_length=1, max_length=32, description="合约代码，如 rb2701")
    direction: str = Field(pattern="^(LONG|SHORT)$")
    offset: str = Field(
        default="OPEN",
        pattern="^(OPEN|CLOSE|CLOSE_TODAY|CLOSE_YESTERDAY)$",
        description="SHFE 平仓需区分 CLOSE_TODAY/CLOSE_YESTERDAY",
    )
    quantity: int = Field(gt=0, le=1000)
    price_type: str = Field(default="LIMIT", pattern="^(LIMIT|MARKET)$")
    limit_price: Decimal | None = None


@router.post("/orders")
async def place_manual_order(payload: ManualOrderIn) -> dict[str, Any]:
    """受理手动下单并投递到交易出口（不直接碰 CTP——单一出口原则）。"""
    # 1) 基础校验（人工优先：不过策略风控）
    symbol = "".join(ch for ch in payload.contract if not ch.isdigit())
    try:
        instrument = get_instrument(symbol)
    except Exception as exc:  # noqa: BLE001  # 注册表查不到 → 400
        raise HTTPException(status_code=400, detail=f"未知合约: {payload.contract}（{exc}）") from exc
    if payload.price_type == "LIMIT" and payload.limit_price is None:
        raise HTTPException(status_code=400, detail="限价单必须提供 limit_price")
    if payload.price_type == "MARKET" and payload.limit_price is not None:
        raise HTTPException(status_code=400, detail="市价单不带限价（由出口侧按对手价转换）")

    command = OrderCommand(
        instance_id="manual",
        contract=payload.contract,
        symbol=instrument.symbol,
        direction=Direction(payload.direction),
        offset=Offset(payload.offset),
        quantity=Quantity(Decimal(payload.quantity)),
        price_type=PriceType(payload.price_type),
        limit_price=Price(payload.limit_price) if payload.limit_price is not None else None,
        signal_id=None,
    )
    bus = MessageBus()
    try:
        await bus.publish_order_command(command_to_payload(command))  # type: ignore[arg-type]
    finally:
        await bus.close()
    return {
        "accepted": True,
        "contract": command.contract,
        "symbol": command.symbol,
        "direction": command.direction.value,
        "offset": command.offset.value,
        "quantity": str(command.quantity),
        "note": "已投递 trade.order——执行结果与成交回报见 trade.order.status / trade.fill",
    }
