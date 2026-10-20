/** 领域类型：与后端 trading router payload 同构（Decimal 以 str 传输）。 */

export interface Position {
  id: number;
  instance_id: string;
  contract: string;
  direction: "LONG" | "SHORT";
  quantity: string;
  avg_price: string | null;
  updated_at: string;
}

export interface Order {
  id: number;
  order_id: string;
  instance_id: string;
  signal_id: string | null;
  contract: string;
  symbol: string;
  direction: "LONG" | "SHORT";
  offset: string;
  quantity: string;
  price_type: string;
  limit_price: string | null;
  status: "PENDING" | "SUBMITTED" | "PARTIAL" | "FILLED" | "CANCELLED" | "REJECTED";
  filled_quantity: string;
  avg_fill_price: string | null;
  reject_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface Trade {
  id: number;
  order_pk: number;
  quantity: string;
  price: string;
  commission: string;
  ts: string;
}

export interface AccountSummary {
  equity: string;
  realized: string;
  unrealized: string;
  position_count: number;
  pnl_date: string | null;
}

export interface HeartbeatEntry {
  [key: string]: unknown;
}

export interface StreamMessage {
  channel: string;
  payload: Record<string, unknown> | null;
}
