/** REST 客户端：对齐后端 trading router（Decimal 以 str 传输，前端原样展示）。 */

import type {
  AccountSummary,
  HeartbeatEntry,
  Order,
  Position,
  Trade,
} from "./types";

const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`);
  if (!resp.ok) {
    throw new Error(`API ${path} -> ${resp.status} ${resp.statusText}`);
  }
  return (await resp.json()) as T;
}

export const api = {
  positions: () => get<{ positions: Position[] }>("/trading/positions"),
  orders: (status?: string) =>
    get<{ orders: Order[] }>(`/trading/orders${status ? `?status=${status}` : ""}`),
  trades: () => get<{ trades: Trade[] }>("/trading/trades"),
  accountSummary: () => get<AccountSummary>("/trading/account-summary"),
  heartbeat: () => get<{ heartbeats: Record<string, HeartbeatEntry | null> }>("/trading/heartbeat"),
  healthz: () => get<Record<string, unknown>>("/healthz"),
};
