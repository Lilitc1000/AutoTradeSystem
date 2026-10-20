/**
 * SSE 订阅：对齐后端 /api/stream（白名单频道）。
 *
 * 浏览器 EventSource 原生断线重连；事件为 data: JSON（{channel, payload}）。
 * store 里 onmessage 分发到对应状态（信号流 / 成交流）。
 */

import type { StreamMessage } from "./types";

export interface StreamHandlers {
  onMessage: (msg: StreamMessage) => void;
  onError?: (err: Event) => void;
}

export function subscribeStream(channels: string[], handlers: StreamHandlers): () => void {
  const source = new EventSource(`/api/stream?channels=${encodeURIComponent(channels.join(","))}`);
  source.onmessage = (event: MessageEvent) => {
    try {
      handlers.onMessage(JSON.parse(event.data) as StreamMessage);
    } catch {
      // 忽略坏帧（代理缓冲/半包）
    }
  };
  source.onerror = (err: Event) => handlers.onError?.(err);
  return () => source.close();
}
