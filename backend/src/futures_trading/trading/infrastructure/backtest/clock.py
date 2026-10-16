"""BacktestClock：虚拟时钟（ClockPort 实现）。

设计文档 7.2：回测中"当前时间"必须显式可控，这是防未来函数的根基——
策略只能看到时钟之前的 bar。实盘侧由 M2 的 SystemClock 实现同一端口。
"""

from __future__ import annotations

from datetime import datetime


class BacktestClock:
    """虚拟时钟：由引擎驱动推进，策略/风控通过它取"当前时间"。"""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, ts: datetime) -> None:
        """推进到指定时刻；只允许向前（回滚=未来函数）。"""
        if ts < self._now:
            raise ValueError(f"时钟回滚被禁止：{self._now} -> {ts}（未来函数风险）")
        self._now = ts
