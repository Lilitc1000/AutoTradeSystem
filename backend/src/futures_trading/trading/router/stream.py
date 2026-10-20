"""实时流 API（M3-a）：SSE 转发 Redis 频道。

为什么 SSE 而非 WebSocket：全部数据流都是服务端→前端单向（行情/信号/
回报/心跳）；SSE 是浏览器原生（断线自动重连、事件源语义），比 WS 少
一半复杂度。前端干预走 REST POST，不需要双向通道。

安全：channels 白名单前缀校验——防止订阅任意内部频道。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import redis.asyncio as redis
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ...common.settings import settings

router = APIRouter(prefix="/api/stream", tags=["stream"])

# 允许订阅的频道前缀（与 message_bus 协议对应）
_ALLOWED_PREFIXES = ("md.bar.", "md.tick.", "signal.outcome", "trade.fill", "heartbeat:")

# 心跳保活间隔（秒）——SSE 经代理时需要定期注释行防断开
_KEEPALIVE_SECONDS = 15


def _validate_channels(channels: list[str]) -> list[str]:
    for channel in channels:
        if not channel or not channel.startswith(_ALLOWED_PREFIXES):
            raise HTTPException(status_code=400, detail=f"不允许订阅的频道: {channel}")
    return channels


@router.get("")
async def stream(channels: str = Query(description="逗号分隔频道，如 signal.outcome,trade.fill")) -> StreamingResponse:
    """SSE 端点：订阅指定频道，实时转发消息。

    事件格式（与 Redis payload 同构）:
        data: {"channel": "signal.outcome", "payload": {...}}
    """
    requested = [c.strip() for c in channels.split(",") if c.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="channels 不能为空")
    _validate_channels(requested)

    async def event_source() -> AsyncIterator[str]:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(*requested)
        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=_KEEPALIVE_SECONDS,
                )
                if message is None:
                    yield ": keepalive\n\n"  # 保活注释行
                    continue
                payload = {
                    "channel": message.get("channel", ""),
                    "payload": json.loads(message["data"]) if message.get("data") else None,
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
        finally:
            await pubsub.unsubscribe()
            await client.close()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
