"""ORM 模型：14 张表（对齐技术设计文档第 8 章）。

约定：
- 所有金额/价格用 Numeric，禁止 float
- 事件表（trades/risk_events/audit_log）append-only，只插入不更新
- 时间列一律 timestamptz，默认 UTC
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

# 通用精度
MONEY = Numeric(20, 8)  # 价格/金额
QTY = Numeric(20, 4)  # 手数（整数语义，Decimal 承载）
RATIO = Numeric(10, 6)  # 比例/回撤


def utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- 品种
class Instrument(Base):
    """合约注册表（对应 domain Instrument 聚合根）。"""

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    contract: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    multiplier: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tick_size: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    margin_rate: Mapped[Decimal] = mapped_column(RATIO, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    sessions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        UniqueConstraint("symbol", "contract", name="uq_instruments_symbol_contract"),
        CheckConstraint("multiplier > 0", name="ck_instruments_multiplier"),
        CheckConstraint("tick_size > 0", name="ck_instruments_tick_size"),
        CheckConstraint("margin_rate > 0 AND margin_rate <= 1", name="ck_instruments_margin_rate"),
    )


# ---------------------------------------------------------------- 策略
class Strategy(Base):
    """策略定义。"""

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    universe: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    versions: Mapped[list[StrategyVersion]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan", order_by="StrategyVersion.version"
    )


class StrategyVersion(Base):
    """不可变参数档位版本链（只增不改）。"""

    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_pk: Mapped[int] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    backtest_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    strategy: Mapped[Strategy] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("strategy_pk", "version", name="uq_strategy_versions_strategy_version"),
        CheckConstraint("version >= 1", name="ck_strategy_versions_version"),
    )


class StrategyInstance(Base):
    """运行实例 = 策略版本 + 运行模式 + 资金切片。"""

    __tablename__ = "strategy_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    strategy_pk: Mapped[int] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    param_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="SHADOW")
    capital_slice: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    slice_peak: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    daily_pnl: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("capital_slice > 0", name="ck_strategy_instances_slice"),
        CheckConstraint("slice_peak >= capital_slice", name="ck_strategy_instances_peak"),
    )


# ---------------------------------------------------------------- 信号与订单
class Signal(Base):
    """信号记录（含被拒绝的，风控有效性的证据）。"""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    strength: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=Decimal("1"))
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (Index("ix_signals_instance_ts", "instance_id", "ts"),)


class Order(Base):
    """订单全生命周期（对应 domain Order 聚合根）。"""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    contract: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    offset: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    price_type: Mapped[str] = mapped_column(String(8), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    filled_quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal("0"))
    avg_fill_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_orders_quantity"),
        CheckConstraint("filled_quantity >= 0 AND filled_quantity <= quantity", name="ck_orders_filled"),
        Index("ix_orders_instance_status", "instance_id", "status"),
    )


class Trade(Base):
    """成交回报（append-only，来自柜台，不可篡改）。"""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_pk: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    commission: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (CheckConstraint("quantity > 0", name="ck_trades_quantity"),)


class Position(Base):
    """持仓快照（按实例 × 合约；由 trades 推导，可重建）。"""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    contract: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False, default=Decimal("0"))
    avg_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (UniqueConstraint("instance_id", "contract", name="uq_positions_instance_contract"),)


# ---------------------------------------------------------------- 风控与记账
class RiskEvent(Base):
    """风控触发与熔断记录（append-only）。"""

    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)  # P0/P1/P2
    instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class DailyPnl(Base):
    """实例 × 日期 盈亏流水。"""

    __tablename__ = "daily_pnl"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    realized: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    unrealized: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    equity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (UniqueConstraint("instance_id", "date", name="uq_daily_pnl_instance_date"),)


class Settlement(Base):
    """期货公司结算单导入与对账结果。"""

    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # simnow / broker / ...
    diff_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (UniqueConstraint("date", "source", name="uq_settlements_date_source"),)


# ---------------------------------------------------------------- 回测
class BacktestRun(Base):
    """回测任务（参数快照引用，保证可复现）。"""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    param_version: Mapped[int] = mapped_column(Integer, nullable=False)
    universe: Mapped[list] = mapped_column(JSONB, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    trades: Mapped[list[BacktestTrade]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class BacktestTrade(Base):
    """回测成交明细。"""

    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_pk: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), nullable=False)
    contract: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    offset: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    commission: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    pnl: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped[BacktestRun] = relationship(back_populates="trades")

    __table_args__ = (Index("ix_backtest_trades_run_ts", "run_pk", "ts"),)


# ---------------------------------------------------------------- 审计
class AuditLog(Base):
    """参数切换、风控修改、手工干预（append-only）。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


__all__ = [
    "Instrument",
    "Strategy",
    "StrategyVersion",
    "StrategyInstance",
    "Signal",
    "Order",
    "Trade",
    "Position",
    "RiskEvent",
    "DailyPnl",
    "Settlement",
    "BacktestRun",
    "BacktestTrade",
    "AuditLog",
]
