/** 手动下单 API：POST /api/trading/orders → trade.order（交易出口执行）。 */

export interface ManualOrderIn {
  contract: string;
  direction: "LONG" | "SHORT";
  offset: "OPEN" | "CLOSE" | "CLOSE_TODAY" | "CLOSE_YESTERDAY";
  quantity: number;
  price_type?: "LIMIT" | "MARKET";
  limit_price?: string | null;
}

export interface OrderStatusEvent {
  contract: string;
  symbol: string;
  instance_id: string;
  signal_id: string | null;
  status: "FILLED" | "PARTIAL" | "ERROR";
  requested?: string;
  filled?: string;
  remaining?: string;
  avg_price?: string | null;
  attempts?: number;
  msg?: string;
}

export async function placeManualOrder(payload: ManualOrderIn): Promise<Record<string, unknown>> {
  const resp = await fetch("/api/trading/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await resp.json();
  if (!resp.ok) {
    throw new Error(String(body?.detail ?? `下单失败 HTTP ${resp.status}`));
  }
  return body as Record<string, unknown>;
}
