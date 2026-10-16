"""内置品种元数据注册表。

M1 回测即用：常见品种的乘数/最小变动价位/保证金比例（近似值，
实盘前应以期货公司实际标准校正——已列入 M4 校准清单）。
PG instruments 表有记录时优先用库里的。
"""

from __future__ import annotations

from decimal import Decimal

from ..trading.domain.model.instrument import ContractStatus, Exchange, Instrument

# symbol -> (name, exchange, multiplier, tick_size, margin_rate)
_SPECS: dict[str, tuple[str, Exchange, Decimal, Decimal, Decimal]] = {
    "rb": ("螺纹钢", Exchange.SHFE, Decimal("10"), Decimal("1"), Decimal("0.12")),
    "hc": ("热轧卷板", Exchange.SHFE, Decimal("10"), Decimal("1"), Decimal("0.12")),
    "i": ("铁矿石", Exchange.DCE, Decimal("100"), Decimal("0.5"), Decimal("0.13")),
    "j": ("焦炭", Exchange.DCE, Decimal("100"), Decimal("0.5"), Decimal("0.15")),
    "jm": ("焦煤", Exchange.DCE, Decimal("60"), Decimal("0.5"), Decimal("0.15")),
    "m": ("豆粕", Exchange.DCE, Decimal("10"), Decimal("1"), Decimal("0.10")),
    "y": ("豆油", Exchange.DCE, Decimal("10"), Decimal("2"), Decimal("0.10")),
    "TA": ("精对苯二甲酸", Exchange.CZCE, Decimal("5"), Decimal("2"), Decimal("0.10")),
    "MA": ("甲醇", Exchange.CZCE, Decimal("10"), Decimal("1"), Decimal("0.10")),
    "CF": ("棉花", Exchange.CZCE, Decimal("5"), Decimal("5"), Decimal("0.10")),
    "IF": ("沪深300股指", Exchange.CFFEX, Decimal("300"), Decimal("0.2"), Decimal("0.12")),
    "IC": ("中证500股指", Exchange.CFFEX, Decimal("200"), Decimal("0.2"), Decimal("0.12")),
    "au": ("黄金", Exchange.SHFE, Decimal("1000"), Decimal("0.02"), Decimal("0.09")),
    "ag": ("白银", Exchange.SHFE, Decimal("15"), Decimal("1"), Decimal("0.12")),
    "cu": ("铜", Exchange.SHFE, Decimal("5"), Decimal("10"), Decimal("0.12")),
}


def get_instrument(symbol: str, contract: str | None = None) -> Instrument:
    """取品种元数据；contract 缺省用 {symbol}9999（回测视角的虚拟主力）。"""
    spec = _SPECS.get(symbol)
    if spec is None:
        raise KeyError(
            f"品种 {symbol} 无内置元数据（已支持: {sorted(_SPECS)}）；"
            "请先在 PG instruments 表登记或扩展 _SPECS"
        )
    name, exchange, multiplier, tick_size, margin_rate = spec
    return Instrument(
        symbol=symbol,
        contract=contract or f"{symbol}9999",
        name=name,
        exchange=exchange,
        multiplier=multiplier,
        tick_size=tick_size,
        margin_rate=margin_rate,
        status=ContractStatus.MAIN,
    )


def known_symbols() -> list[str]:
    return sorted(_SPECS)
