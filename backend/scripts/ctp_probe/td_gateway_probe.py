"""td worker 真连验证（生产路径）：起 CtpTradeGateway（worker 子进程）

    认证链 → 结算单确认 → 发限价单 → 挂单 → 撤单 → 退出

与 py_order_probe.py 的区别：那个是**独立探针**（直连 CTP so）；
本脚本走**生产代码路径**（CtpTradeGateway + multiprocessing worker），
验证的就是昨日子进程隔离改造后的真实链路。

用法（容器内）：PYTHONPATH=src python3 scripts/ctp_probe/td_gateway_probe.py [合约] [价格]
"""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from futures_trading.trading.application.dtos import OrderCommand
from futures_trading.common.types import Price, Quantity
from futures_trading.trading.domain.model.order import Offset, PriceType
from futures_trading.trading.domain.model.signal import Direction
from futures_trading.trading.infrastructure.ctp.adapters import CtpConfig, CtpTradeGateway

CONTRACT = sys.argv[1] if len(sys.argv) > 1 else "rb2701"
PRICE = sys.argv[2] if len(sys.argv) > 2 else "3070"
SYMBOL = "".join(ch for ch in CONTRACT if ch.isalpha())


class ProbeBus:
    """最小 MessageBus：打印发布内容。"""

    async def publish_bar(self, payload: dict) -> None:
        print(f"[bar] {payload.get('symbol')} {payload.get('interval_minutes')}m close={payload.get('close')}")

    async def publish(self, channel: str, payload: dict) -> None:
        print(f"[{channel}] order={payload.get('order_id')} status={payload.get('status')} "
              f"filled={payload.get('filled_quantity')} avg={payload.get('avg_fill_price')}")


async def main() -> int:
    gw = CtpTradeGateway(CtpConfig.from_settings(), ProbeBus())
    try:
        return await _run_probe(gw)
    finally:
        # 保证 worker 被优雅停止（异常路径跳过 stop 会让 daemon 被杀时
        # CTP 析构 segfault——日盘实踩）
        try:
            await gw.stop()
        except Exception:  # noqa: BLE001
            pass


async def _run_probe(gw: CtpTradeGateway) -> int:
    gw = CtpTradeGateway(CtpConfig.from_settings(), ProbeBus())
    print(f"[probe] 启动交易 worker（合约 {CONTRACT} 限价 {PRICE} 开多 1 手）...")
    await gw.start()
    print(f"[probe] 会话就绪（front_id={gw._front_id} session_id={gw._session_id}）")

    # 发单（指令经 cmd_q 下发 worker 子进程）
    order = gw.submit(
        OrderCommand(
            instance_id="td-gw-probe",
            contract=CONTRACT,
            symbol=SYMBOL,
            direction=Direction.LONG,
            offset=Offset.OPEN,
            quantity=Quantity(Decimal("1")),
            price_type=PriceType.LIMIT,
            limit_price=Price(Decimal(PRICE)),
            signal_id="gw-probe",
        )
    )
    print(f"[probe] 已提交 order_id={order.order_id[-12:]}")

    # 等委托回报（挂单/成交/拒单）
    for _ in range(80):
        await asyncio.sleep(0.1)
        if order.status.value != "PENDING":
            break
    print(f"[probe] 回报后状态: {order.status.value} filled={order.filled_quantity}")

    # 撤单
    gw.cancel(order.order_id)
    for _ in range(60):
        await asyncio.sleep(0.1)
        if order.is_final:
            break
    print(f"[probe] 撤单后状态: {order.status.value}")

    # 资金查询（验证 worker 查询链路）
    try:
        balance = gw.balance_snapshot()
        print(f"[probe] 可用资金: {balance}")
    except RuntimeError as exc:
        print(f"[probe] 资金查询失败: {exc}")

    await gw.stop()
    ok = order.is_final
    print(f"[probe] {'PASS' if ok else 'FAIL'}: final_status={order.status.value}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))