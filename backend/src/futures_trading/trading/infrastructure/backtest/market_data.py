"""BacktestMarketData：多品种时间轴重放（MarketDataPort 实现）。

- subscribe：声明回测涉及的品种
- timeline：全部品种 bar 的时间戳并集（对齐不同品种交易日历差异）
- latest_bar：返回当前虚拟时刻该品种的 bar（此前时刻）
- at/advance：引擎按时间轴步进
"""

from __future__ import annotations

from datetime import datetime

from ....common.errors import ValidationError
from ...application.dtos import Bar


class BacktestMarketData:
    """历史行情重放器。"""

    def __init__(self, bars_by_symbol: dict[str, list[Bar]]) -> None:
        """
        bars_by_symbol: symbol -> 按时间升序的 bar 列表。
        """
        if not bars_by_symbol:
            raise ValidationError("回测行情不能为空")
        self._bars = bars_by_symbol
        self._index: dict[str, int] = {symbol: -1 for symbol in bars_by_symbol}
        self._timeline = sorted({bar.ts for bars in bars_by_symbol.values() for bar in bars})
        if not self._timeline:
            raise ValidationError("回测行情时间轴为空")
        self._cursor = 0
        self._current_ts = self._timeline[0]

    # ---- MarketDataPort ----

    def subscribe(self, symbols: list[str]) -> None:
        missing = [s for s in symbols if s not in self._bars]
        if missing:
            raise ValidationError(f"品种无历史数据：{missing}")

    def latest_bar(self, symbol: str, interval_minutes: int) -> Bar | None:  # noqa: ARG002  # 端口签名
        if symbol not in self._bars:
            raise ValidationError(f"未订阅品种 {symbol}")
        idx = self._index[symbol]
        if idx < 0:
            return None
        return self._bars[symbol][idx]

    # ---- 重放控制（引擎用）----

    @property
    def timeline(self) -> list[datetime]:
        return self._timeline

    @property
    def symbols(self) -> list[str]:
        return list(self._bars)

    @property
    def current_ts(self) -> datetime:
        return self._current_ts

    def at_beginning(self) -> datetime:
        self._cursor = 0
        self._current_ts = self._timeline[0]
        for symbol in self._bars:
            self._index[symbol] = -1
        self._catch_up()
        return self._current_ts

    def advance(self) -> datetime | None:
        """推进到下一个时间戳；到末尾返回 None。"""
        self._cursor += 1
        if self._cursor >= len(self._timeline):
            return None
        self._current_ts = self._timeline[self._cursor]
        self._catch_up()
        return self._current_ts

    def bars_at_current(self) -> dict[str, Bar]:
        """当前时刻各品种最新 bar（可能缺某些品种——日历差异）。"""
        result: dict[str, Bar] = {}
        for symbol, idx in self._index.items():
            if idx >= 0:
                result[symbol] = self._bars[symbol][idx]
        return result

    def _catch_up(self) -> None:
        for symbol, bars in self._bars.items():
            idx = self._index[symbol]
            while idx + 1 < len(bars) and bars[idx + 1].ts <= self._current_ts:
                idx += 1
            self._index[symbol] = idx
