/** 交易域 store：REST 快照 + SSE 实时流（信号/成交）。 */

import { defineStore } from "pinia";
import { ref } from "vue";
import { api } from "../api/client";
import { subscribeStream } from "../api/stream";
import type { AccountSummary, HeartbeatEntry, Order, Position, StreamMessage, Trade } from "../api/types";

export const useTradingStore = defineStore("trading", () => {
  const positions = ref<Position[]>([]);
  const orders = ref<Order[]>([]);
  const trades = ref<Trade[]>([]);
  const summary = ref<AccountSummary | null>(null);
  const heartbeats = ref<Record<string, HeartbeatEntry | null>>({});
  const signalFeed = ref<Record<string, unknown>[]>([]);
  const fillFeed = ref<Record<string, unknown>[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  let _unsubscribe: (() => void) | null = null;

  async function refresh() {
    loading.value = true;
    error.value = null;
    try {
      const [pos, ord, trd, sum, hb] = await Promise.all([
        api.positions(),
        api.orders(),
        api.trades(),
        api.accountSummary(),
        api.heartbeat(),
      ]);
      positions.value = pos.positions;
      orders.value = ord.orders;
      trades.value = trd.trades;
      summary.value = sum;
      heartbeats.value = hb.heartbeats;
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      loading.value = false;
    }
  }

  function _handle(msg: StreamMessage) {
    if (msg.channel === "signal.outcome" && msg.payload) {
      signalFeed.value.unshift(msg.payload);
      if (signalFeed.value.length > 100) signalFeed.value.pop();
    } else if (msg.channel === "trade.fill" && msg.payload) {
      fillFeed.value.unshift(msg.payload);
      if (fillFeed.value.length > 100) fillFeed.value.pop();
    }
  }

  function startStream() {
    if (_unsubscribe) return;
    _unsubscribe = subscribeStream(["signal.outcome", "trade.fill", "md.bar.rb"], {
      onMessage: _handle,
    });
  }

  function stopStream() {
    _unsubscribe?.();
    _unsubscribe = null;
  }

  return {
    positions,
    orders,
    trades,
    summary,
    heartbeats,
    signalFeed,
    fillFeed,
    loading,
    error,
    refresh,
    startStream,
    stopStream,
  };
});
