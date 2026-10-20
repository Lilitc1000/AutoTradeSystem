"""CTP 适配器：域模型 ↔ CTP 结构体映射（纯函数，可单测）。

M2-a 只做映射与骨架；真连 SimNow 联调在 M2-b（账号就绪后）。
openctp 库懒加载：未安装时本模块仍可 import（字符串注解 + TYPE_CHECKING）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ....common.types import ZERO, Price, Quantity
from ...application.dtos import OrderCommand, Tick
from ...domain.model.order import Offset, OrderStatus, PriceType
from ...domain.model.signal import Direction

if TYPE_CHECKING:  # pragma: no cover - 仅为类型注解
    pass


# ---------------------------------------------------------------- 枚举映射
def direction_to_ctp(direction: Direction) -> str:
    """域方向 → CTP 买卖方向字符（'0'=买 '1'=卖，与 ctp 库 DirectionType 一致）。"""
    return "0" if direction is Direction.LONG else "1"


def offset_to_ctp(offset: Offset) -> str:
    """域开平 → CTP 开平标志字符。

    CTP CombOffsetFlag：'0'=开仓 '1'=平昨 '3'=平今（SHFE 专用）。
    SHFE 平仓不区分今昔会被拒（"持仓不足"）——signal_pipeline 按持仓
    今昨构成拆分报单，故这里支持三种平仓。
    """
    if offset is Offset.OPEN:
        return "0"
    if offset is Offset.CLOSE_TODAY:
        return "3"
    return "1"  # CLOSE / CLOSE_YESTERDAY


def price_type_to_ctp(price_type: PriceType) -> str:
    """域价格类型 → CTP 报单价格类型字符。

    CTP 编码反直觉（ThostFtdcUserApiDataType.h）：'1'=任意价(市价) '2'=限价。
    曾错填 LIMIT→'1'（发成市价单）→ SHFE 错误码 79「不被支持的报单类型」
    → 订单 a→5 被撤（夜盘联调 21:19 实锤）。勿改回。
    """
    return "2" if price_type is PriceType.LIMIT else "1"


def ctp_to_order_status(ctp_status: str, ctp_order_submit_status: str = "0") -> OrderStatus:
    """CTP 订单状态字符 → 域订单状态。

    ctp_status（OrderStatus 字段）：
      '0'=全部成交 '1'=部分成交还在队列 '2'=未成交还在队列
      '3'=未成交不在队列 '4'=撤单 '5'=未知
    ctp_order_submit_status（OrderSubmitStatus）：
      '0'=已经提交 '1'=撤单已提交 '2'=修改已提交 '3'=已经接受 '4'=报单已经被拒绝 '5'=刚提交
    """
    if ctp_order_submit_status == "4":
        return OrderStatus.REJECTED
    return {
        "0": OrderStatus.FILLED,
        "1": OrderStatus.PARTIAL,
        "2": OrderStatus.SUBMITTED,
        "3": OrderStatus.CANCELLED,
        "5": OrderStatus.PENDING,
    }.get(ctp_status, OrderStatus.PENDING)


# ---------------------------------------------------------------- 报单映射
def command_to_ctp_input(command: OrderCommand) -> dict[str, Any]:
    """OrderCommand → CTP InputOrder 字段字典（适配器填 InstrumentID/OrderRef 等后使用）。

    纯映射：不含任何连接状态。单测直接覆盖。
    """
    return {
        "Direction": direction_to_ctp(command.direction),
        "CombOffsetFlag": offset_to_ctp(command.offset),
        "CombHedgeFlag": "1",  # 投机
        "LimitPrice": float(command.limit_price) if command.limit_price is not None else 0.0,
        "OrderPriceType": price_type_to_ctp(command.price_type),
        "VolumeTotalOriginal": int(Decimal(command.quantity)),
        "TimeCondition": "3",  # 当日有效
        "VolumeCondition": "1",  # 任何数量
        "ContingentCondition": "1",  # 立即
        "ForceCloseReason": "0",  # 非强平
        "MinVolume": 1,
        "IsAutoSuspend": 0,
    }


# ---------------------------------------------------------------- 回报映射
def ctp_trade_to_fill(trade_field: dict[str, Any]) -> dict[str, Any]:
    """CTP TradeField（属性字典）→ 成交记录（与 trade_gateway 落库格式一致）。"""
    return {
        "contract": str(trade_field.get("InstrumentID", "")),
        "direction": "LONG" if str(trade_field.get("Direction", "0")) == "0" else "SHORT",
        "offset": "OPEN" if str(trade_field.get("OffsetFlag", "0")) == "0" else "CLOSE",
        "quantity": Decimal(str(trade_field.get("Volume", 0))),
        "price": Decimal(str(trade_field.get("Price", 0))),
        "commission": Decimal(str(trade_field.get("Commission", 0)) or 0),
        "trade_id": str(trade_field.get("TradeID", "")),
        "order_ref": str(trade_field.get("OrderRef", "")),
        "ts": str(trade_field.get("TradeTime", "")),
    }


def ctp_depth_to_tick(field: dict[str, Any], symbol: str) -> Tick:
    """CTP DepthMarketData（属性字典）→ 域 Tick。"""
    ts_str = str(field.get("UpdateTime") or "") + "." + str(field.get("UpdateMillisec") or "0").zfill(3)
    from datetime import datetime

    try:
        ts = datetime.strptime(ts_str, "%H:%M:%S.%f").replace(
            year=datetime.now().year, month=datetime.now().month, day=datetime.now().day
        )
    except ValueError:
        ts = datetime.now()
    last = field.get("LastPrice")
    return Tick(
        symbol=symbol,
        ts=ts,
        last_price=Price(Decimal(str(last)) if last not in (None, "") else ZERO),
        volume=Quantity(Decimal(str(field.get("Volume", 0) or 0))),
        open_interest=Quantity(Decimal(str(field.get("OpenInterest", 0) or 0))),
        bid_price=Price(Decimal(str(field.get("BidPrice1", 0) or 0))),
        ask_price=Price(Decimal(str(field.get("AskPrice1", 0) or 0))),
    )


__all__ = [
    "direction_to_ctp",
    "offset_to_ctp",
    "price_type_to_ctp",
    "ctp_to_order_status",
    "command_to_ctp_input",
    "ctp_trade_to_fill",
    "ctp_depth_to_tick",
]
