"""initial schema: 14 tables

Revision ID: 0001
Revises: None
Create Date: 2026-10-16

对齐技术设计文档第 8 章 + persistence/models.py。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("contract", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("multiplier", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("tick_size", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("margin_rate", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("sessions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("multiplier > 0", name="ck_instruments_multiplier"),
        sa.CheckConstraint("tick_size > 0", name="ck_instruments_tick_size"),
        sa.CheckConstraint(
            "margin_rate > 0 AND margin_rate <= 1", name="ck_instruments_margin_rate"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_instruments"),
        sa.UniqueConstraint("symbol", "contract", name="uq_instruments_symbol_contract"),
    )

    op.create_table(
        "strategies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("universe", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_strategies"),
        sa.UniqueConstraint("strategy_id", name="uq_strategies_strategy_id"),
    )

    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_pk", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("backtest_ref", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_strategy_versions_version"),
        sa.ForeignKeyConstraint(
            ["strategy_pk"], ["strategies.id"], name="fk_strategy_versions_strategy_pk_strategies"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_strategy_versions"),
        sa.UniqueConstraint("strategy_pk", "version", name="uq_strategy_versions_strategy_version"),
    )

    op.create_table(
        "strategy_instances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_pk", sa.Integer(), nullable=False),
        sa.Column("param_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="SHADOW"),
        sa.Column("capital_slice", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("slice_peak", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("daily_pnl", sa.Numeric(precision=20, scale=8), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("capital_slice > 0", name="ck_strategy_instances_slice"),
        sa.CheckConstraint("slice_peak >= capital_slice", name="ck_strategy_instances_peak"),
        sa.ForeignKeyConstraint(
            ["strategy_pk"], ["strategies.id"], name="fk_strategy_instances_strategy_pk_strategies"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_strategy_instances"),
        sa.UniqueConstraint("instance_id", name="uq_strategy_instances_instance_id"),
    )

    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.String(length=32), nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("strength", sa.Numeric(precision=10, scale=6), nullable=False, server_default="1"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_signals"),
        sa.UniqueConstraint("signal_id", name="uq_signals_signal_id"),
    )
    op.create_index("ix_signals_instance_ts", "signals", ["instance_id", "ts"], unique=False)

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("signal_id", sa.String(length=32), nullable=True),
        sa.Column("contract", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("offset", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("price_type", sa.String(length=8), nullable=False),
        sa.Column("limit_price", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("filled_quantity", sa.Numeric(precision=20, scale=4), nullable=False, server_default="0"),
        sa.Column("avg_fill_price", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_orders_quantity"),
        sa.CheckConstraint(
            "filled_quantity >= 0 AND filled_quantity <= quantity", name="ck_orders_filled"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.UniqueConstraint("order_id", name="uq_orders_order_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
    )
    op.create_index("ix_orders_instance_status", "orders", ["instance_id", "status"], unique=False)

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_pk", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("commission", sa.Numeric(precision=20, scale=8), nullable=False, server_default="0"),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_trades_quantity"),
        sa.ForeignKeyConstraint(["order_pk"], ["orders.id"], name="fk_trades_order_pk_orders"),
        sa.PrimaryKeyConstraint("id", name="pk_trades"),
    )

    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("contract", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=4), nullable=False, server_default="0"),
        sa.Column("avg_price", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_positions"),
        sa.UniqueConstraint("instance_id", "contract", name="uq_positions_instance_contract"),
    )

    op.create_table(
        "risk_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_risk_events"),
    )

    op.create_table(
        "daily_pnl",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("realized", sa.Numeric(precision=20, scale=8), nullable=False, server_default="0"),
        sa.Column("unrealized", sa.Numeric(precision=20, scale=8), nullable=False, server_default="0"),
        sa.Column("equity", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_daily_pnl"),
        sa.UniqueConstraint("instance_id", "date", name="uq_daily_pnl_instance_date"),
    )

    op.create_table(
        "settlements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("diff_summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_settlements"),
        sa.UniqueConstraint("date", "source", name="uq_settlements_date_source"),
    )

    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("param_version", sa.Integer(), nullable=False),
        sa.Column("universe", postgresql.JSONB(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("report", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_runs"),
        sa.UniqueConstraint("run_id", name="uq_backtest_runs_run_id"),
    )

    op.create_table(
        "backtest_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_pk", sa.Integer(), nullable=False),
        sa.Column("contract", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("offset", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("commission", sa.Numeric(precision=20, scale=8), nullable=False, server_default="0"),
        sa.Column("pnl", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_pk"], ["backtest_runs.id"], name="fk_backtest_trades_run_pk_backtest_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_trades"),
    )
    op.create_index("ix_backtest_trades_run_ts", "backtest_trades", ["run_pk", "ts"], unique=False)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("backtest_trades")
    op.drop_table("backtest_runs")
    op.drop_table("settlements")
    op.drop_table("daily_pnl")
    op.drop_table("risk_events")
    op.drop_table("positions")
    op.drop_table("trades")
    op.drop_table("orders")
    op.drop_table("signals")
    op.drop_table("strategy_instances")
    op.drop_table("strategy_versions")
    op.drop_table("strategies")
    op.drop_table("instruments")
