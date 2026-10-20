/** Store 单测：REST 刷新 + SSE 消息分发（mock fetch / EventSource）。 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useTradingStore } from "../src/stores/trading";

const FIXTURES = {
  "/api/trading/positions": { positions: [{ id: 1, instance_id: "i1", contract: "rb2701", direction: "LONG", quantity: "3", avg_price: "3050", updated_at: "2026-10-20T02:00:00Z" }] },
  "/api/trading/orders": { orders: [] },
  "/api/trading/trades": { trades: [] },
  "/api/trading/account-summary": { equity: "101900", realized: "1200", unrealized: "-300", position_count: 1, pnl_date: "2026-10-20" },
  "/api/trading/heartbeat": { heartbeats: { md_gateway: { ts: 1 }, strategy_core: null, trade_gateway: { ts: 2 } } },
};

class FakeEventSource {
  static last: FakeEventSource | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }
  url: string;
  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true,
    json: async () => FIXTURES[url as keyof typeof FIXTURES] ?? {},
  })));
  vi.stubGlobal("EventSource", FakeEventSource);
});

describe("trading store", () => {
  it("refresh 拉齐 REST 快照", async () => {
    const store = useTradingStore();
    await store.refresh();
    expect(store.positions).toHaveLength(1);
    expect(store.positions[0].contract).toBe("rb2701");
    expect(store.summary?.equity).toBe("101900");
    expect(Object.keys(store.heartbeats)).toEqual(["md_gateway", "strategy_core", "trade_gateway"]);
  });

  it("startStream 订阅频道并分发消息", () => {
    const store = useTradingStore();
    store.startStream();
    expect(FakeEventSource.last).not.toBeNull();
    expect(FakeEventSource.last?.url).toContain("signal.outcome");
    expect(FakeEventSource.last?.url).toContain("trade.fill");

    FakeEventSource.last?.onmessage?.({
      data: JSON.stringify({ channel: "signal.outcome", payload: { status: "EXECUTED" } }),
    } as MessageEvent);
    expect(store.signalFeed).toHaveLength(1);
    expect(store.signalFeed[0]).toEqual({ status: "EXECUTED" });

    store.stopStream();
    expect(FakeEventSource.last?.closed).toBe(true);
  });

  it("重复 startStream 不会重复订阅", () => {
    const store = useTradingStore();
    store.startStream();
    const first = FakeEventSource.last;
    store.startStream();
    expect(FakeEventSource.last).toBe(first);
  });
});
