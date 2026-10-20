/** 账号 API：对齐后端 /api/accounts（凭证只写不读——服务端脱敏）。 */

export interface TradeAccountView {
  id: number;
  name: string;
  broker_id: string;
  user_id: string;
  app_id: string;
  md_front: string;
  trade_front: string;
  is_active: boolean;
  has_auth_code: boolean;
  last_test_ok: boolean | null;
  last_test_at: string | null;
  last_test_msg: string | null;
  created_at: string;
}

export interface AccountPayload {
  name: string;
  broker_id: string;
  user_id: string;
  password: string;
  auth_code: string;
  app_id: string;
  md_front: string;
  trade_front: string;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`/api/accounts${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`API ${path} -> ${resp.status}: ${detail.slice(0, 200)}`);
  }
  return (await resp.json()) as T;
}

export const accountsApi = {
  list: () => req<{ accounts: TradeAccountView[] }>("GET", ""),
  create: (payload: AccountPayload) => req<TradeAccountView>("POST", "", payload),
  update: (id: number, payload: Partial<AccountPayload>) =>
    req<TradeAccountView>("PUT", `/${id}`, payload),
  remove: (id: number) => req<{ deleted: number }>("DELETE", `/${id}`),
  activate: (id: number) => req<TradeAccountView>("POST", `/${id}/activate`),
  test: (id: number) => req<{ ok: boolean; msg: string }>("POST", `/${id}/test`),
};
