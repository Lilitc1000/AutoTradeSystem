"""Strategy / StrategyInstance 聚合根：策略定义与运行实例。

- Strategy：策略逻辑定义 + 不可变参数档位版本链（只增不改）
- StrategyInstance：运行实例 = 某版本 + 运行模式 + 资金切片，
  持有持仓集合；实例内独立盈亏、独立回撤熔断（设计文档 5.3）
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from ....common.errors import InvariantViolationError
from ....common.types import ZERO, Money, Quantity
from .position import Position
from .signal import Direction


class StrategyType(StrEnum):
    """策略类型（v1 仅实现趋势类的运行框架）。"""

    TREND_ATR = "TREND_ATR"  # 趋势跟踪 + ATR 仓位管理（主线）
    GRID = "GRID"  # 震荡网格（演进项）


class InstanceMode(StrEnum):
    """实例运行模式。"""

    SHADOW = "SHADOW"  # 影子：只记录信号不下单
    SIM = "SIM"  # 仿真：模拟柜台自动执行
    LIVE = "LIVE"  # 实盘
    PAUSED = "PAUSED"  # 暂停


@dataclass(frozen=True)
class ParameterSet:
    """不可变参数档位。版本一经创建即冻结，调整只能新增版本。"""

    version: int
    params: Mapping[str, Any]
    created_at: datetime
    backtest_ref: str | None = None  # 回测报告引用（验证证据）

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvariantViolationError("参数版本号必须 ≥ 1")
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


@dataclass
class Strategy:
    """策略定义。"""

    strategy_id: str
    type: StrategyType
    universe: frozenset[str]  # 交易的品种集合（symbol，非合约）
    versions: list[ParameterSet] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise InvariantViolationError("strategy_id 不能为空")
        if not self.universe:
            raise InvariantViolationError("universe 不能为空")

    def add_version(self, parameter_set: ParameterSet) -> None:
        """新增参数档位：版本号必须严格递增（只增不改）。"""
        if self.versions and parameter_set.version <= self.versions[-1].version:
            raise InvariantViolationError(
                f"参数版本号必须严格递增：{parameter_set.version} <= {self.versions[-1].version}"
            )
        self.versions.append(parameter_set)

    @property
    def latest_version(self) -> ParameterSet:
        if not self.versions:
            raise InvariantViolationError(f"策略 {self.strategy_id} 尚未定义任何参数版本")
        return self.versions[-1]

    def version_of(self, version: int) -> ParameterSet:
        for candidate in self.versions:
            if candidate.version == version:
                return candidate
        raise InvariantViolationError(f"策略 {self.strategy_id} 不存在版本 {version}")


@dataclass
class StrategyInstance:
    """运行中的策略实例。"""

    instance_id: str
    strategy_id: str
    param_version: int
    mode: InstanceMode
    capital_slice: Money  # 资金切片（记账口径）
    slice_peak: Money  # 切片历史峰值（回撤熔断基准）
    daily_pnl: Money = Money(ZERO)  # 当日已实现盈亏
    positions: dict[str, Position] = field(default_factory=dict)  # key: contract

    def __post_init__(self) -> None:
        if not self.instance_id:
            raise InvariantViolationError("instance_id 不能为空")
        if Decimal(self.capital_slice) <= ZERO:
            raise InvariantViolationError("资金切片必须为正")
        if Decimal(self.slice_peak) < Decimal(self.capital_slice):
            # 允许初始峰值等于切片；小于则数据错误
            raise InvariantViolationError("slice_peak 不能小于 capital_slice")

    def position_for(self, contract: str, direction: Direction, multiplier: Decimal) -> Position:
        """get-or-create：同实例同合约同方向只有一个持仓对象。"""
        existing = self.positions.get(contract)
        if existing is not None:
            if existing.direction is not direction:
                raise InvariantViolationError(
                    f"实例 {self.instance_id} 在 {contract} 上已存在反向持仓，请先平仓"
                )
            return existing
        position = Position(
            instance_id=self.instance_id,
            contract=contract,
            direction=direction,
            multiplier=multiplier,
        )
        self.positions[contract] = position
        return position

    def close_position(self, contract: str) -> None:
        """移除已平光的持仓记录。"""
        position = self.positions.get(contract)
        if position is not None and position.is_flat():
            del self.positions[contract]

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if not p.is_flat()]

    def used_quantity(self, contract: str) -> Quantity:
        position = self.positions.get(contract)
        return position.quantity if position is not None else Quantity(ZERO)

    def update_slice_peak(self, current_slice_equity: Money) -> None:
        if Decimal(current_slice_equity) > Decimal(self.slice_peak):
            self.slice_peak = current_slice_equity
