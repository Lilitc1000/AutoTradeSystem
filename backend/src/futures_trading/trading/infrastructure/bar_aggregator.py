"""tick → bar 聚合器（实盘策略以 bar 为决策单位，与回测同构）。

纯函数式实现：喂 tick，周期切换时吐出闭合的 bar。
聚合规则与主流软件一致：开盘=周期首 tick，收盘=周期末 tick，
最高/最低取极值，成交量/持仓累加。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from ...common.types import Price, Quantity
from ..application.dtos import Bar, Tick


class BarAggregator:
    """单品种单周期的 tick 聚合器。"""

    def __init__(self, symbol: str, interval_minutes: int) -> None:
        if interval_minutes <= 0:
            raise ValueError("interval_minutes 必须为正")
        self.symbol = symbol
        self.interval_minutes = interval_minutes
        self._bucket: datetime | None = None
        self._open: Price | None = None
        self._high: Price | None = None
        self._low: Price | None = None
        self._close: Price | None = None
        self._volume: Decimal = Decimal("0")
        self._open_interest: Decimal = Decimal("0")

    def _bucket_of(self, ts: datetime) -> datetime:
        """tick 所属周期起点（向下取整到 interval 边界）。"""
        midnight = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        minutes = int((ts - midnight).total_seconds() // 60)
        return midnight + timedelta(minutes=minutes - minutes % self.interval_minutes)

    def on_tick(self, tick: Tick) -> Bar | None:
        """喂入 tick；周期切换时返回上一根闭合 bar，否则返回 None。"""
        bucket = self._bucket_of(tick.ts)
        closed: Bar | None = None
        if self._bucket is not None and bucket != self._bucket:
            closed = self._close_current()
            self._reset()
        if self._bucket is None:
            self._bucket = bucket
        price = tick.last_price
        if self._open is None:
            self._open = price
        self._high = price if self._high is None else max(self._high, price)
        self._low = price if self._low is None else min(self._low, price)
        self._close = price
        self._volume += Decimal(tick.volume)
        self._open_interest += Decimal(tick.open_interest)
        return closed

    def flush(self) -> Bar | None:
        """强制闭合当前周期（收盘/停机时用）。"""
        return self._close_current()

    def _close_current(self) -> Bar | None:
        if self._bucket is None or self._open is None or self._close is None:
            return None
        return Bar(
            symbol=self.symbol,
            interval_minutes=self.interval_minutes,
            ts=self._bucket,
            open=self._open,
            high=self._high if self._high is not None else self._close,
            low=self._low if self._low is not None else self._close,
            close=self._close,
            volume=Quantity(self._volume),
            open_interest=Quantity(self._open_interest),
        )

    def _reset(self) -> None:
        self._bucket = None
        self._open = None
        self._high = None
        self._low = None
        self._close = None
        self._volume = Decimal("0")
        self._open_interest = Decimal("0")


__all__ = ["BarAggregator"]
