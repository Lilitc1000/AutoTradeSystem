"""Instrument 聚合根测试：元数据校验、保证金/名义价值换算、取整、交易时段。"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

import pytest

from futures_trading.common.errors import ValidationError
from futures_trading.common.types import Price
from futures_trading.trading.domain.model.instrument import (
    ContractStatus,
    Exchange,
    Instrument,
    TradingSession,
    is_contract_of,
)


@pytest.fixture
def rb() -> Instrument:
    return Instrument(
        symbol="rb",
        contract="rb2510",
        name="螺纹钢",
        exchange=Exchange.SHFE,
        multiplier=Decimal("10"),
        tick_size=Decimal("1"),
        margin_rate=Decimal("0.12"),
    )


class TestValidation:
    def test_valid(self, rb: Instrument) -> None:
        assert rb.instrument_id == "SHFE.rb2510"

    def test_empty_symbol_rejected(self) -> None:
        with pytest.raises(ValidationError, match="symbol 不能为空"):
            Instrument("", "rb2510", "x", Exchange.SHFE, Decimal("10"), Decimal("1"), Decimal("0.12"))

    def test_contract_must_start_with_symbol(self) -> None:
        with pytest.raises(ValidationError, match="必须以 symbol"):
            Instrument("rb", "hc2510", "x", Exchange.SHFE, Decimal("10"), Decimal("1"), Decimal("0.12"))

    def test_non_positive_multiplier_rejected(self) -> None:
        with pytest.raises(ValidationError, match="multiplier 必须为正"):
            Instrument("rb", "rb2510", "x", Exchange.SHFE, Decimal("0"), Decimal("1"), Decimal("0.12"))

    def test_margin_rate_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError, match="margin_rate"):
            Instrument("rb", "rb2510", "x", Exchange.SHFE, Decimal("10"), Decimal("1"), Decimal("1.5"))


class TestCalculations:
    def test_notional_per_lot(self, rb: Instrument) -> None:
        assert rb.notional_per_lot(Price(Decimal("3500"))) == Decimal("35000")

    def test_margin_per_lot(self, rb: Instrument) -> None:
        # 35000 * 0.12 = 4200
        assert rb.margin_per_lot(Price(Decimal("3500"))) == Decimal("4200")

    def test_round_price(self, rb: Instrument) -> None:
        assert rb.round_price(Price(Decimal("3500.4"))) == Price(Decimal("3500"))
        assert rb.round_price(Price(Decimal("3500.6"))) == Price(Decimal("3501"))

    def test_is_tradable(self, rb: Instrument) -> None:
        assert rb.is_tradable()
        delisted = Instrument(
            symbol="rb",
            contract="rb0000",
            name="已退市",
            exchange=Exchange.SHFE,
            multiplier=Decimal("10"),
            tick_size=Decimal("1"),
            margin_rate=Decimal("0.12"),
            status=ContractStatus.DELISTED,
        )
        assert not delisted.is_tradable()


class TestTradingSession:
    def test_day_session_contains(self) -> None:
        session = TradingSession(start=time(9, 0), end=time(15, 0))
        assert session.contains(time(12, 0))
        assert not session.contains(time(16, 0))

    def test_night_session_crosses_midnight(self) -> None:
        session = TradingSession(start=time(21, 0), end=time(2, 30))
        assert session.contains(time(23, 0))
        assert session.contains(time(1, 0))  # 次日凌晨
        assert not session.contains(time(3, 0))


class TestIsContractOf:
    def test_true(self) -> None:
        assert is_contract_of("rb", "rb2510")

    def test_false(self) -> None:
        assert not is_contract_of("rb", "hc2510")
