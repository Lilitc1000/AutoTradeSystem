"""最小复现：asyncio 事件循环下 CtpMarketData 是否稳定收 tick。

对照基准：scripts/ctp_probe/py_md_sub.py（time.sleep 阻塞循环）稳定。
若本脚本 0 tick + 4097 → asyncio 与 CTP 线程模型冲突。
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/workspace/backend/src")

from futures_trading.common.instrument_registry import get_instrument  # noqa: E402
from futures_trading.trading.infrastructure.ctp.adapters import (  # noqa: E402
    CtpConfig,
    CtpMarketData,
)
from futures_trading.trading.infrastructure.messaging.bus import MessageBus  # noqa: E402


async def main() -> None:
    bus = MessageBus()
    md = CtpMarketData(
        CtpConfig.from_settings(), bus, {"rb2701": get_instrument("rb")}, interval_minutes=1
    )
    md.subscribe(["rb2701"])
    await md.start()
    for _ in range(40):
        await asyncio.sleep(1)
        print(f"tick_count={md._tick_count}", flush=True)
    await md.stop()
    print(f"FINAL tick_count={md._tick_count}", flush=True)


asyncio.run(main())
