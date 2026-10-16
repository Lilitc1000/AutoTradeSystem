"""strategy_core：策略引擎进程。

M0 空跑：仅心跳 + 日志。M2 运行全部 StrategyInstance：
行情 → 信号 → 组合预检 → 仓位计算 → 风控终检 → OrderCommand → Redis 总线。
"""

from __future__ import annotations

import asyncio

from .base import ProcessBase


class StrategyCore(ProcessBase):
    name = "strategy_core"

    async def _run(self) -> None:
        self.logger.info("策略引擎就绪（M0 空跑：信号链路将在 M2 接入）")
        while not self.stopped:
            await asyncio.sleep(1)


def main() -> None:
    StrategyCore().run()


if __name__ == "__main__":
    main()
