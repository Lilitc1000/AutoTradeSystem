"""Instrument 聚合根：品种与合约的注册表。

职责：维护合约元数据（乘数、最小变动价位、保证金比例、交易时段），
保证这些数据的业务有效性。策略绑定 symbol，本聚合根管理合约生命周期
（主力标记、退市状态）与换月。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from enum import StrEnum

from ....common.errors import ValidationError
from ....common.types import Money, Price, round_to_tick


class Exchange(StrEnum):
    """国内期货交易所。"""

    SHFE = "SHFE"  # 上海期货交易所
    DCE = "DCE"  # 大连商品交易所
    CZCE = "CZCE"  # 郑州商品交易所
    CFFEX = "CFFEX"  # 中国金融期货交易所
    INE = "INE"  # 上海国际能源交易中心


class ContractStatus(StrEnum):
    """合约状态。"""

    ACTIVE = "ACTIVE"  # 可交易，非主力
    MAIN = "MAIN"  # 主力合约
    NON_MAIN = "NON_MAIN"
    DELISTED = "DELISTED"  # 已退市


@dataclass(frozen=True)
class TradingSession:
    """交易时段。start > end 表示跨午夜（夜盘）。"""

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        if self.start <= self.end:
            return self.start <= moment <= self.end
        return moment >= self.start or moment <= self.end


@dataclass(frozen=True)
class Instrument:
    """合约元数据。

    约定：contract 以 symbol 开头（如 symbol="rb", contract="rb2510"）。
    """

    symbol: str
    contract: str
    name: str
    exchange: Exchange
    multiplier: Decimal  # 合约乘数（每手吨数）
    tick_size: Decimal  # 最小变动价位（元）
    margin_rate: Decimal  # 保证金比例（0~1）
    status: ContractStatus = ContractStatus.ACTIVE
    sessions: tuple[TradingSession, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValidationError("symbol 不能为空")
        if not self.contract:
            raise ValidationError("contract 不能为空")
        if not self.contract.startswith(self.symbol):
            raise ValidationError(f"contract({self.contract}) 必须以 symbol({self.symbol}) 开头")
        if self.multiplier <= Decimal("0"):
            raise ValidationError("multiplier 必须为正")
        if self.tick_size <= Decimal("0"):
            raise ValidationError("tick_size 必须为正")
        if not Decimal("0") < self.margin_rate <= Decimal("1"):
            raise ValidationError("margin_rate 必须在 (0, 1] 区间")

    @property
    def instrument_id(self) -> str:
        return f"{self.exchange.value}.{self.contract}"

    def notional_per_lot(self, price: Price) -> Money:
        """每手合约名义价值。"""
        return Money(Decimal(price) * self.multiplier)

    def margin_per_lot(self, price: Price) -> Money:
        """每手合约保证金。"""
        return Money(self.notional_per_lot(price) * self.margin_rate)

    def round_price(self, price: Price) -> Price:
        """按最小变动价位取整。"""
        return Price(round_to_tick(Decimal(price), self.tick_size))

    def is_tradable(self) -> bool:
        return self.status is not ContractStatus.DELISTED


def is_contract_of(symbol: str, contract: str) -> bool:
    """判断 contract 是否属于 symbol 品种。"""
    return contract.startswith(symbol)
