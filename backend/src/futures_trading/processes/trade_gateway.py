"""trade_gateway：交易网关进程。

M0 空跑：仅心跳 + 日志。M2 接入 CTP 交易适配器（OrderPort），
消费 OrderCommand、维护订单状态机、回报写 PG + Redis。
"""

from __future__ import annotations

import asyncio

from .base import ProcessBase


class TradeGateway(ProcessBase):
    name = "trade_gateway"

    async def _run(self) -> None:
        self.logger.info("交易网关就绪（M0 空跑：CTP 适配器将在 M2 接入）")
        while not self.stopped:
            await asyncio.sleep(1)


def main() -> None:
    TradeGateway().run()


if __name__ == "__main__":
    main()
