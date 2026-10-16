"""回测 API：任务创建 / 列表 / 报告 / 参数扫描。

M1 同步执行（小数据回测秒级）；大扫描任务异步化留 M3。
报告落 PG（backtest_runs + backtest_trades），重启不丢。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...common.instrument_registry import get_instrument
from ..application.runner import run_backtest
from ..domain.model.strategy import StrategyType
from ..infrastructure.backtest.engine import BacktestConfig
from ..infrastructure.parquet_store import load_bars
from ..infrastructure.persistence.database import database
from ..infrastructure.persistence.models import BacktestRun, BacktestTrade
from ..infrastructure.scanner import grid_scan

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class JobRequest(BaseModel):
    """创建回测任务请求。"""

    symbols: list[str] = Field(min_length=1)
    interval: str = "day"
    params: dict[str, Any] = Field(default_factory=dict)
    risk_per_trade_pct: Decimal = Decimal("0.01")
    stop_multiplier: Decimal = Decimal("2")
    initial_capital: Decimal = Decimal("1000000")


class ScanRequest(JobRequest):
    """参数扫描请求（最多 2 个参数）。"""

    grid: dict[str, list[Any]] = Field(min_length=1)


@router.post("/jobs")
async def create_job(req: JobRequest) -> dict[str, object]:
    report = _run(req)
    await _persist(report, req)
    data: dict[str, object] = report.to_dict()
    return data


@router.post("/scan")
async def create_scan(req: ScanRequest) -> dict[str, Any]:
    config, bars, instruments = _prepare(req)
    try:
        results = grid_scan(config, bars, instruments, req.grid, max_params=2)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "grid": {k: [str(v) for v in vs] for k, vs in req.grid.items()},
        "results": [
            {
                "params": {k: str(v) for k, v in r.params.items()},
                "total_return_pct": r.total_return_pct,
                "max_drawdown_pct": r.max_drawdown_pct,
                "sharpe": r.sharpe,
                "round_trips": r.round_trips,
            }
            for r in results
        ],
    }


@router.get("/runs")
async def list_runs(limit: int = 20) -> dict[str, Any]:
    from sqlalchemy import select

    async with database.session() as session:
        result = await session.execute(
            select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
        )
        runs = result.scalars().all()
        return {
            "runs": [
                {
                    "run_id": r.run_id,
                    "strategy_id": r.strategy_id,
                    "param_version": r.param_version,
                    "universe": r.universe,
                    "interval_minutes": r.interval_minutes,
                    "status": r.status,
                    "created_at": r.created_at.isoformat(),
                }
                for r in runs
            ]
        }


@router.get("/reports/{run_id}")
async def get_report(run_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    async with database.session() as session:
        result = await session.execute(select(BacktestRun).where(BacktestRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail=f"回测任务 {run_id} 不存在")
        trades_result = await session.execute(
            select(BacktestTrade).where(BacktestTrade.run_pk == run.id).order_by(BacktestTrade.ts)
        )
        trades = [
            {
                "contract": t.contract,
                "direction": t.direction,
                "offset": t.offset,
                "quantity": str(t.quantity),
                "price": str(t.price),
                "commission": str(t.commission),
                "pnl": str(t.pnl) if t.pnl is not None else None,
                "ts": t.ts.isoformat(),
            }
            for t in trades_result.scalars().all()
        ]
        return {
            "run_id": run.run_id,
            "status": run.status,
            "report": run.report,
            "trades": trades,
        }


# ---------------------------------------------------------------- internal
def _run(req: JobRequest) -> Any:
    config, bars, instruments = _prepare(req)
    return run_backtest(config, bars, instruments, run_id=f"bt-{uuid.uuid4().hex[:12]}")


def _prepare(req: JobRequest) -> tuple[BacktestConfig, dict[str, list], dict[str, Any]]:
    interval = "day" if req.interval == "day" else req.interval
    bars: dict[str, list] = {}
    instruments: dict[str, Any] = {}
    for symbol in req.symbols:
        loaded = load_bars(symbol, interval)
        if not loaded:
            raise HTTPException(
                status_code=404,
                detail=f"品种 {symbol} 无 {interval} 历史数据（先跑 scripts/fetch_history.py 导入）",
            )
        bars[symbol] = loaded
        instruments[symbol] = get_instrument(symbol)
    config = BacktestConfig(
        strategy_type=StrategyType.TREND_ATR,
        param_version=1,
        universe=tuple(req.symbols),
        params=req.params,
        initial_capital=req.initial_capital,
        risk_per_trade_pct=req.risk_per_trade_pct,
        stop_multiplier=req.stop_multiplier,
    )
    return config, bars, instruments


async def _persist(report: Any, req: JobRequest) -> None:
    interval_minutes = 1440 if req.interval == "day" else int(req.interval.replace("m", ""))
    async with database.session() as session:
        run = BacktestRun(
            run_id=report.run_id,
            strategy_id="trend_atr_v1",
            param_version=1,
            universe=list(req.symbols),
            interval_minutes=interval_minutes,
            start_date=datetime.now(UTC),
            end_date=datetime.now(UTC),
            status="DONE",
            report=report.to_dict(),
        )
        session.add(run)
        await session.flush()
        for trade in report.trades:
            session.add(
                BacktestTrade(
                    run_pk=run.id,
                    contract=str(trade.get("contract", "")),
                    direction=str(trade.get("direction", "")),
                    offset=str(trade.get("offset", "")),
                    quantity=Decimal(str(trade.get("quantity", 0))),
                    price=Decimal(str(trade.get("price", 0))),
                    commission=Decimal(str(trade.get("commission", 0))),
                    pnl=Decimal(str(trade["pnl"])) if trade.get("pnl") is not None else None,
                    ts=datetime.now(UTC),
                )
            )


__all__ = ["router"]
