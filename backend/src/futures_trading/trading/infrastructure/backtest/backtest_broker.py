"""BacktestBroker：系统内置虚拟账户 + 模拟撮合。

实现 OrderPort 语义（submit/cancel/query），但完全本地运行：
- submit：创建 PENDING 挂单（不立即成交）
- on_bar：按 bar 撮合——市价单按 bar.open ± 滑点成交；
  限价买单在 bar.low <= limit 时成交，限价卖单在 bar.high >= limit 时成交
- 成交驱动 Position 变化；手续费从虚拟余额扣除

与 CTPAdapter 的关键差异仅在数据来源与撮合方，domain 层无感知。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from ....common.errors import ValidationError
from ....common.types import ZERO, Money, Price, Quantity
from ...application.dtos import Bar, OrderCommand
from ...domain.model.instrument import Instrument
from ...domain.model.order import Offset, Order, OrderStatus, PriceType
from ...domain.model.position import Fill, Position
from ...domain.model.signal import Direction
from .slippage_model import SlippageModel


class BacktestBroker:
    """回测专用虚拟账户 + 模拟撮合器。"""

    def __init__(
        self,
        initial_capital: Money,
        slippage: SlippageModel | None = None,
        instruments: Mapping[str, Instrument] | None = None,
    ) -> None:
        """
        instruments: contract -> Instrument 映射（乘数查询用）。
        使用 dict[str, object] 避免 infrastructure 反向依赖具体类型签名；
        实际传入 trading.domain.model.instrument.Instrument。
        """
        if Decimal(initial_capital) <= ZERO:
            raise ValidationError("initial_capital 必须为正")
        self.balance = Money(Decimal(initial_capital))
        self.initial_capital = Money(Decimal(initial_capital))
        self._slippage = slippage or SlippageModel()
        self._instruments = instruments or {}
        self.orders: dict[str, Order] = {}
        self.positions: dict[tuple[str, str], Position] = {}  # (instance_id, contract) -> Position
        self.trade_log: list[dict[str, object]] = []  # 成交流水（审计/报告用）
        self._order_seq = 0

    # ---- OrderPort 语义 ----

    def submit(self, command: OrderCommand) -> Order:
        """创建挂单（PENDING），等待 on_bar 撮合。"""
        if command.price_type is PriceType.LIMIT and command.limit_price is None:
            raise ValidationError("限价单必须提供 limit_price")
        self._order_seq += 1
        order = Order(
            order_id=f"BT-{self._order_seq:06d}",
            idempotency_key=command.idempotency_key,
            instance_id=command.instance_id,
            contract=command.contract,
            symbol=command.symbol,
            direction=command.direction,
            offset=command.offset,
            quantity=command.quantity,
            price_type=command.price_type,
            limit_price=command.limit_price,
            signal_id=command.signal_id,
            status=OrderStatus.PENDING,
        )
        # 幂等：同键订单已存在则直接返回既有订单
        for existing in self.orders.values():
            if existing.idempotency_key == order.idempotency_key:
                return existing
        # 本地撮合器无网络延迟：挂单即送达
        order.submit()
        self.orders[order.order_id] = order
        return order

    def cancel(self, order_id: str) -> Order:
        order = self._require(order_id)
        order.cancel()
        return order

    def query(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)

    # ---- 撮合 ----

    def on_bar(self, bar: Bar) -> list[Order]:
        """用一根 K 线撮合全部待成交订单，返回本根 bar 成交的订单列表。"""
        filled: list[Order] = []
        for order in list(self.orders.values()):
            if order.is_final:
                continue
            if order.symbol != bar.symbol:
                continue
            price = self._execution_price(order, bar)
            if price is None:
                continue
            quantity = order.remaining_quantity
            order.fill(quantity, price)
            self._apply_fill(order, quantity, price)
            filled.append(order)
        return filled

    def on_bar_all(self, bars: dict[str, Bar]) -> list[Order]:
        """多品种同时刻撮合（回测引擎用）。"""
        filled: list[Order] = []
        for bar in bars.values():
            filled.extend(self.on_bar(bar))
        return filled

    def _execution_price(self, order: Order, bar: Bar) -> Price | None:
        """返回本根 bar 上该订单的成交价；不可成交返回 None。"""
        if order.price_type is PriceType.MARKET:
            # 市价单：按 bar 开盘价 ± 滑点
            return self._slippage.execution_price(bar.open, order.direction, self._tick_size(order))
        # 限价单：价格穿透判定
        limit = Decimal(order.limit_price) if order.limit_price is not None else None
        if limit is None:
            return None
        if order.direction is Direction.LONG:
            # 限价买入：bar 最低价不高于限价才成交
            if Decimal(bar.low) <= limit:
                return Price(limit)
        else:
            if Decimal(bar.high) >= limit:
                return Price(limit)
        return None

    def _apply_fill(self, order: Order, quantity: Quantity, price: Price) -> None:
        multiplier = self._multiplier(order.contract)
        key = (order.instance_id, order.contract)
        position = self.positions.get(key)
        if position is None:
            position = Position(
                instance_id=order.instance_id,
                contract=order.contract,
                direction=order.direction,
                multiplier=multiplier,
            )
            self.positions[key] = position
        fill = Fill(quantity=quantity, price=price)
        lots = int(Decimal(quantity))
        commission = self._slippage.commission(lots)
        realized: Decimal | None = None
        if order.offset is Offset.OPEN:
            position.add(fill)
            self.balance = Money(Decimal(self.balance) - Decimal(commission))
        else:
            realized = position.reduce(fill)
            self.balance = Money(Decimal(self.balance) + Decimal(realized) - Decimal(commission))
        self.trade_log.append(
            {
                "order_id": order.order_id,
                "instance_id": order.instance_id,
                "contract": order.contract,
                "direction": str(order.direction),
                "offset": str(order.offset),
                "quantity": quantity,
                "price": price,
                "commission": commission,
                "pnl": realized,  # 仅平仓成交有值（毛盈亏，未扣手续费）
                "ts": order.updated_at,
                "balance": self.balance,
            }
        )

    # ---- 查询 ----

    def equity(self, last_prices: dict[str, Price]) -> Money:
        """账户权益 = 虚拟余额 + 全部持仓浮动盈亏。"""
        floating = Decimal("0")
        for position in self.positions.values():
            price = last_prices.get(position.contract)
            if price is not None and not position.is_flat():
                floating += Decimal(position.unrealized_pnl(price))
        return Money(Decimal(self.balance) + floating)

    def filled_orders(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status is OrderStatus.FILLED]

    # ---- 内部 ----

    def _require(self, order_id: str) -> Order:
        order = self.orders.get(order_id)
        if order is None:
            raise ValidationError(f"订单 {order_id} 不存在")
        return order

    def _tick_size(self, order: Order) -> Decimal:
        instrument = self._instruments.get(order.contract)
        tick = getattr(instrument, "tick_size", None)
        return Decimal(tick) if tick is not None else Decimal("1")

    def _multiplier(self, contract: str) -> Decimal:
        instrument = self._instruments.get(contract)
        multiplier = getattr(instrument, "multiplier", None)
        return Decimal(multiplier) if multiplier is not None else Decimal("1")
