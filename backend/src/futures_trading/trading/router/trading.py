"""交易域查询 API（M3-a）：持仓 / 订单 / 成交 / 信号 / 账户摘要。

只读观测——永不直接下单（干预指令走专用通道，见 api_dashboard docstring）。
payload 与 Redis 频道 JSON 同构，前端 store 可直接消费。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..infrastructure.persistence.database import database
from ..infrastructure.persistence.models import DailyPnl, Order, Position, Signal, Trade

router = APIRouter(prefix="/api/trading", tags=["trading"])


def _fmt(value: Decimal | None) -> str | None:
    """Decimal → str（去尾零；format 'f' 避开 normalize 的科学计数法）。"""
    if value is None:
        return None
    return format(value.normalize(), "f")


def _row_to_dict(row: Any) -> dict[str, Any]:
    """SQLAlchemy 行 → JSON 字典（Decimal → str 保精度，去尾零）。"""
    out: dict[str, Any] = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, Decimal):
            value = _fmt(value)
        elif isinstance(value, datetime):
            value = value.isoformat()
        out[column.name] = value
    return out


@asynccontextmanager
async def _session() -> AsyncIterator[Any]:
    async with database.session() as session:
        yield session


@router.get("/positions")
async def list_positions(instance_id: str | None = None) -> dict[str, object]:
    """当前持仓（可按实例过滤）。"""
    async with _session() as session:
        stmt = select(Position).order_by(Position.contract)
        if instance_id:
            stmt = stmt.where(Position.instance_id == instance_id)
        rows = (await session.execute(stmt)).scalars().all()
    return {"positions": [_row_to_dict(r) for r in rows]}


@router.get("/orders")
async def list_orders(
    status: str | None = Query(default=None, description="PENDING/SUBMITTED/PARTIAL/FILLED/CANCELLED/REJECTED"),
    instance_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    """订单列表（默认最新 100 条，可按状态/实例过滤）。"""
    async with _session() as session:
        stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(Order.status == status.upper())
        if instance_id:
            stmt = stmt.where(Order.instance_id == instance_id)
        rows = (await session.execute(stmt)).scalars().all()
    return {"orders": [_row_to_dict(r) for r in rows]}


@router.get("/trades")
async def list_trades(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, object]:
    """成交明细（append-only，最新在前）。"""
    async with _session() as session:
        stmt = select(Trade).order_by(Trade.ts.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return {"trades": [_row_to_dict(r) for r in rows]}


@router.get("/signals")
async def list_signals(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, object]:
    """信号历史（最新在前）。"""
    async with _session() as session:
        stmt = select(Signal).order_by(Signal.id.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return {"signals": [_row_to_dict(r) for r in rows]}


@router.get("/account-summary")
async def account_summary() -> dict[str, object]:
    """账户摘要：最新权益 + 当日盈亏 + 持仓市值汇总。

    权益取最新日期的 daily_pnl（无记录时返回零值占位）。
    """
    async with _session() as session:
        pnl_stmt = select(DailyPnl).order_by(DailyPnl.date.desc()).limit(1)
        pnl = (await session.execute(pnl_stmt)).scalars().first()
        pos_stmt = select(Position)
        positions = (await session.execute(pos_stmt)).scalars().all()
    equity = pnl.equity if pnl else Decimal("0")
    realized = pnl.realized if pnl else Decimal("0")
    unrealized = pnl.unrealized if pnl else Decimal("0")
    return {
        "equity": _fmt(equity),
        "realized": _fmt(realized),
        "unrealized": _fmt(unrealized),
        "position_count": len(positions),
        "pnl_date": pnl.date.isoformat() if pnl else None,
    }


@router.get("/heartbeat")
async def worker_heartbeat() -> dict[str, object]:
    """三 worker 心跳快照。

    key 与 ProcessBase/strategy_core 写入侧一致：
    `{prefix}:proc:{name}:hb`（时间戳，TTL 15s）+ `:mem`（RSS KB）。
    此前误用 `heartbeat:{name}` 导致前端全显离线（实修）。
    """
    import redis.asyncio as redis

    from ...common.settings import settings

    prefix = settings.redis_key_prefix
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        # trade_gateway 不设独立进程（交易在 strategy_core SIM 模式内），
        # 列它会永远显示离线——误导
        names = ("md_gateway", "strategy_core")
        beats: dict[str, object] = {}
        for name in names:
            ts = await client.get(f"{prefix}:proc:{name}:hb")
            mem = await client.get(f"{prefix}:proc:{name}:mem")
            if ts:
                beats[name] = {"ts": ts, "mem_kb": int(mem) if mem else None}
            else:
                beats[name] = None
    finally:
        await client.close()
    return {"heartbeats": beats}
