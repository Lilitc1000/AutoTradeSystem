"""trade_gateway：交易出口进程（M3-e2）——**单一 CTP 交易连接**。

为什么独立成进程（方案 C）：
- 手动下单（前端）与策略信号（strategy_core）都经 Redis `trade.order`
  频道进来，由 ExecutionService 统一执行——同一条链路（追单/滑点/风控），
  手动单不是二等公民
- 只有一个 td 连接（SimNow 并发限制下必须），策略进程崩溃不影响交易
  出口（挂单/持仓状态存活——7×24 无人值守的关键分层）
- 成交回报由 CtpTradeGateway 自动发 `trade.fill`（strategy_core 消费）
- 执行结果另发 `trade.order.status`（新频道）供前端追踪手动单

行情：只订阅 md.bar.* 缓存最新 bar 作为定价基准（不做聚合——那是
md_gateway 的职责）。

--dry-run：不连 CTP，只消费指令并打印（验证总线链路/调试，不占连接）。
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from ..common.instrument_registry import get_instrument
from ..common.types import Price, Quantity
from ..trading.application.dtos import Bar, OrderCommand, Tick
from ..trading.domain.model.order import Direction, Offset, PriceType
from ..trading.infrastructure.messaging.bus import MessageBus
from .base import ProcessBase

ORDER_CHANNEL = "trade.order"
STATUS_CHANNEL = "trade.order.status"


def _symbol_of(contract: str) -> str:
    """合约代码 → 品种符号（rb2601 → rb）。"""
    return "".join(ch for ch in contract if not ch.isdigit())


def _payload_to_command(payload: dict[str, object]) -> OrderCommand:
    """总线 JSON → OrderCommand（协议见 bus.py：trade.order）。"""
    limit = payload.get("limit_price")
    return OrderCommand(
        instance_id=str(payload.get("instance_id", "")),
        contract=str(payload.get("contract", "")),
        symbol=str(payload.get("symbol", "")) or _symbol_of(str(payload.get("contract", ""))),
        direction=Direction(str(payload.get("direction", "LONG"))),
        offset=Offset(str(payload.get("offset", "OPEN"))),
        quantity=Quantity(Decimal(str(payload.get("quantity", "0")))),
        price_type=PriceType(str(payload.get("price_type", "LIMIT"))),
        limit_price=Price(Decimal(str(limit))) if limit else None,
        signal_id=(str(payload["signal_id"]) if payload.get("signal_id") else None),
    )


async def _consume_orders(
    executor: object,
    bus: MessageBus,
    logger: object,
    *,
    dry_run: bool = False,
) -> None:
    """订阅 trade.order → 执行 → 结果发 trade.order.status。"""
    if not dry_run:
        assert hasattr(executor, "execute")
    async for _channel, payload in bus.subscribe(ORDER_CHANNEL):
        try:
            command = _payload_to_command(payload)
        except (ValueError, ArithmeticError) as exc:
            logger.warning("指令解析失败，丢弃: %s payload=%s", exc, payload)  # type: ignore[attr-defined]
            continue
        logger.info(  # type: ignore[attr-defined]
            "执行指令: contract=%s dir=%s offset=%s qty=%s type=%s",
            command.contract, command.direction.value, command.offset.value,
            command.quantity, command.price_type.value,
        )
        if dry_run:
            logger.info("[dry-run] 不下单：%s", command.contract)  # type: ignore[attr-defined]
            continue
        try:
            result = await executor.execute(command)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - 单笔失败不拖垮出口
            logger.exception("指令执行异常: %s", exc)  # type: ignore[attr-defined]
            await bus.publish(
                STATUS_CHANNEL,
                {
                    "contract": command.contract,
                    "symbol": command.symbol,
                    "instance_id": command.instance_id,
                    "signal_id": command.signal_id,
                    "status": "ERROR",
                    "msg": str(exc),
                },
            )
            continue
        await bus.publish(
            STATUS_CHANNEL,
            {
                "contract": command.contract,
                "symbol": command.symbol,
                "instance_id": command.instance_id,
                "signal_id": command.signal_id,
                "status": "FILLED" if result.completed else "PARTIAL",
                "requested": str(result.requested),
                "filled": str(result.filled),
                "remaining": str(result.remaining),
                "avg_price": str(result.avg_price) if result.avg_price else None,
                "attempts": result.attempts,
            },
        )
        logger.info(  # type: ignore[attr-defined]
            "指令完成: filled=%s/%s avg=%s attempts=%s",
            result.filled, result.requested, result.avg_price, result.attempts,
        )


async def _consume_bars(
    latest_bars: dict[str, Bar], bus: MessageBus, symbols: list[str], logger: object
) -> None:
    """订阅 md.bar.{symbol} 缓存最新 bar（ExecutionService 定价基准）。"""
    async for _channel, payload in bus.subscribe(*[f"md.bar.{s}" for s in symbols]):
        try:
            latest_bars[str(payload.get("symbol", ""))] = Bar(
                symbol=str(payload.get("symbol", "")),
                interval_minutes=int(payload.get("interval_minutes", 1)),
                ts=payload["ts"],  # type: ignore[arg-type]
                open=Price(Decimal(str(payload.get("open", "0")))),
                high=Price(Decimal(str(payload.get("high", "0")))),
                low=Price(Decimal(str(payload.get("low", "0")))),
                close=Price(Decimal(str(payload.get("close", "0")))),
                volume=Quantity(Decimal(str(payload.get("volume", "0")))),
            )
        except (ValueError, ArithmeticError) as exc:
            logger.warning("bar 解析失败: %s", exc)  # type: ignore[attr-defined]


class TradeGateway(ProcessBase):
    """交易出口：订阅 trade.order → ExecutionService → CTP（单连接）。"""

    name = "trade_gateway"

    def __init__(self, symbols: list[str], *, dry_run: bool = False) -> None:
        super().__init__()
        self._symbols = symbols
        self._dry_run = dry_run

    async def _run(self) -> None:
        bus = MessageBus()
        latest_bars: dict[str, Bar] = {}

        def tick_provider(contract: str) -> Tick | None:
            bar = latest_bars.get(contract) or latest_bars.get(_symbol_of(contract))
            if bar is None:
                return None
            return Tick(symbol=contract, ts=bar.ts, last_price=bar.close)

        if self._dry_run:
            self.logger.info("dry-run 模式：订阅 %s（不连 CTP）", ORDER_CHANNEL)
            await _consume_orders(None, bus, self.logger, dry_run=True)
            return

        from ..trading.application.execution import ExecutionService  # noqa: PLC0415
        from ..trading.infrastructure.ctp.adapters import (  # noqa: PLC0415 - CTP 扩展懒加载
            CtpConfig,
            CtpTradeGateway,
        )

        config = await CtpConfig.from_active_account()
        instruments = {s: get_instrument(s) for s in self._symbols}
        gateway = CtpTradeGateway(config, bus, instruments)
        self.logger.info("交易出口启动：账号=%s 品种=%s", config.user_id, self._symbols)
        await gateway.start()
        executor = ExecutionService(gateway, tick_provider, instruments)
        try:
            await asyncio.gather(
                _consume_orders(executor, bus, self.logger),
                _consume_bars(latest_bars, bus, self._symbols, self.logger),
            )
        finally:
            self.logger.info("交易出口退出，stop() ...")
            await gateway.stop()
            await bus.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="交易出口（订阅 trade.order 统一执行）")
    parser.add_argument("--symbols", default="rb", help="订阅品种（定价基准，逗号分隔）")
    parser.add_argument(
        "--dry-run", action="store_true", help="不连 CTP，只消费指令并打印（调试/验证总线）"
    )
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    TradeGateway(symbols, dry_run=args.dry_run).run()


if __name__ == "__main__":
    main()
