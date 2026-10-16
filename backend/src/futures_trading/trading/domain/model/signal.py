"""Signal 值对象：策略产出的交易意图。

Signal 只是意图，不保证成交；能否成交由组合预检、仓位计算与
风控终检依次裁决（设计文档 P3：策略与组合分层）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from ....common.types import ONE


class Direction(StrEnum):
    """交易方向。"""

    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def opposite(self) -> Direction:
        return Direction.SHORT if self is Direction.LONG else Direction.LONG


@dataclass(frozen=True)
class Signal:
    """一条交易意图。"""

    instance_id: str  # 产出信号的策略实例
    symbol: str  # 品种（非具体合约）
    direction: Direction
    ts: datetime
    strength: Decimal = ONE  # 信号强度（0~1），预留
    reason: str = ""  # 人类可读的产生原因（审计用）

    @property
    def signal_id(self) -> str:
        """确定性 ID：同一实例同一原因的信号只算一个。"""
        raw = f"{self.instance_id}|{self.symbol}|{self.direction.value}|{self.ts.isoformat()}|{self.reason}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
