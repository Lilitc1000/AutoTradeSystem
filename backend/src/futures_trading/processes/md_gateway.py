"""md_gateway：行情网关进程。

M0 空跑：仅心跳 + 日志。M2 接入 CTP 行情适配器（MarketDataPort），
订阅全品种行情并规范化写入 Redis 总线。
"""

from __future__ import annotations

import asyncio

from .base import ProcessBase


class MdGateway(ProcessBase):
    name = "md_gateway"

    async def _run(self) -> None:
        self.logger.info("行情网关就绪（M0 空跑：CTP 适配器将在 M2 接入）")
        while not self.stopped:
            await asyncio.sleep(1)


def main() -> None:
    MdGateway().run()


if __name__ == "__main__":
    main()
