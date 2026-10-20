"""交易账号服务（M3-e1）：CRUD + 连接测试 + 激活账号解析。

- 凭证 Fernet 加密落库（common.crypto）；列表/查询**永不返回明文**
- 连接测试：起一次性 td worker（lifetime 短），等 READY（认证→登录→
  结算单确认全过）即成功——与生产 CtpTradeGateway 同代码路径
- 激活账号唯一（设激活即原子撤销其他）
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from ...common.crypto import decrypt, encrypt
from ..infrastructure.persistence.database import database
from ..infrastructure.persistence.models import TradeAccount

# 连接测试等待上限（秒）——与 CtpTradeGateway._drain_until_ready 一致
_TEST_TIMEOUT = 25
_TEST_LIFETIME = 40  # worker 存活上限（兜底自杀）
_RETRY_DELAY = 60  # SIGSEGV 后等会话释放再重试（秒）——柜台侧会话注销约 1-2 分钟
_PROBE_COOLDOWN = 60  # 两次探测之间的最小间隔（CTP 会话释放窗口，柜台侧 1-2 分钟）
_LAST_PROBE_END = 0.0  # 上次探测结束时刻（进程内节流）


@asynccontextmanager
async def _session() -> AsyncIterator[Any]:
    async with database.session() as session:
        yield session


def _to_view(row: TradeAccount) -> dict[str, Any]:
    """行 → 前端视图（脱敏：无任何密文字段）。"""
    return {
        "id": row.id,
        "name": row.name,
        "broker_id": row.broker_id,
        "user_id": row.user_id,
        "app_id": row.app_id,
        "md_front": row.md_front,
        "trade_front": row.trade_front,
        "is_active": row.is_active,
        "has_auth_code": bool(row.encrypted_auth_code),
        "last_test_ok": row.last_test_ok,
        "last_test_at": row.last_test_at.isoformat() if row.last_test_at else None,
        "last_test_msg": row.last_test_msg,
        "created_at": row.created_at.isoformat(),
    }


class AccountService:
    """账号 CRUD + 连接测试（worker 启动器可注入——测试用 Fake）。"""

    def __init__(self, worker_runner: Any = None) -> None:
        # worker_runner(queue, config) → None：起认证探针进程（测试注入 Fake）
        self._worker_runner = worker_runner

    # ---- CRUD ----

    async def list_accounts(self) -> list[dict[str, Any]]:
        async with _session() as session:
            rows = (await session.execute(select(TradeAccount).order_by(TradeAccount.id))).scalars().all()
        return [_to_view(r) for r in rows]

    async def create_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = TradeAccount(
            name=payload["name"].strip(),
            broker_id=payload.get("broker_id", "9999").strip(),
            user_id=payload["user_id"].strip(),
            encrypted_password=encrypt(payload["password"]),
            app_id=payload.get("app_id", "simnow_client_test").strip(),
            encrypted_auth_code=encrypt(payload.get("auth_code", "") or ""),
            md_front=payload["md_front"].strip(),
            trade_front=payload["trade_front"].strip(),
        )
        async with _session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            view = _to_view(row)
        # 首个账号自动激活（省得用户手动点）
        if not await self.get_active_id():
            await self.activate(row.id)
            view["is_active"] = True
        return view

    async def update_account(self, account_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        async with _session() as session:
            row = await session.get(TradeAccount, account_id)
            if row is None:
                raise KeyError(f"账号不存在: {account_id}")
            for field in ("name", "broker_id", "app_id", "md_front", "trade_front"):
                if field in payload and payload[field] is not None:
                    setattr(row, field, str(payload[field]).strip())
            if "user_id" in payload and payload["user_id"]:
                row.user_id = str(payload["user_id"]).strip()
            if payload.get("password"):
                row.encrypted_password = encrypt(payload["password"])
            if payload.get("auth_code") is not None:
                row.encrypted_auth_code = encrypt(payload["auth_code"])
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return _to_view(row)

    async def delete_account(self, account_id: int) -> None:
        async with _session() as session:
            row = await session.get(TradeAccount, account_id)
            if row is None:
                raise KeyError(f"账号不存在: {account_id}")
            await session.delete(row)
            await session.commit()

    async def activate(self, account_id: int) -> dict[str, Any]:
        """设为激活账号（原子撤销其他——单账号约束）。"""
        async with _session() as session:
            row = await session.get(TradeAccount, account_id)
            if row is None:
                raise KeyError(f"账号不存在: {account_id}")
            await session.execute(update(TradeAccount).values(is_active=False))
            row.is_active = True
            await session.commit()
            await session.refresh(row)
            return _to_view(row)

    async def get_active_id(self) -> int | None:
        async with _session() as session:
            row = (
                await session.execute(select(TradeAccount.id).where(TradeAccount.is_active.is_(True)).limit(1))
            ).scalars().first()
        return int(row) if row is not None else None

    async def get_active_account(self) -> TradeAccount | None:
        """激活账号行（调用方负责解密——勿跨会话持有 ORM 对象）。"""
        account_id = await self.get_active_id()
        if account_id is None:
            return None
        async with _session() as session:
            row: TradeAccount | None = await session.get(TradeAccount, account_id)
            return row

    async def active_config_dict(self) -> dict[str, Any] | None:
        """激活账号 → CtpConfig 入参（已解密）；无激活账号返回 None。"""
        account = await self.get_active_account()
        if account is None:
            return None
        return {
            "broker_id": account.broker_id,
            "user_id": account.user_id,
            "password": decrypt(account.encrypted_password),
            "app_id": account.app_id,
            "auth_code": decrypt(account.encrypted_auth_code) if account.encrypted_auth_code else "",
            "md_front": account.md_front,
            "trade_front": account.trade_front,
        }

    # ---- 连接测试 ----

    async def test_connection(self, account_id: int) -> dict[str, Any]:
        """起一次性 td worker 验证凭证（READY=认证+登录+结算单确认全过）。

        与生产同代码路径（run_ctp_worker mode="td"）——测试通过的账号
        就是能交易的账号。结果落库（last_test_ok/at/msg）。

        偶发 SIGSEGV(-11)：上一个 CTP 会话未完全释放就起新 worker 会撞
        （密集重试必然遇到）——等一会儿重试一次再下结论，避免把时序问题
        误报成"凭证错误"。
        """
        ok, msg, worker = await self._probe_once(account_id)
        if not ok and getattr(worker, "exitcode", None) == -11:
            await asyncio.sleep(_RETRY_DELAY)
            ok, msg, worker = await self._probe_once(account_id)

        async with _session() as session:
            row = await session.get(TradeAccount, account_id)
            if row is not None:
                row.last_test_ok = ok
                row.last_test_at = datetime.now(UTC)
                row.last_test_msg = msg
                await session.commit()
        return {"ok": ok, "msg": msg}

    async def _probe_once(self, account_id: int) -> tuple[bool, str, Any]:
        """单次探测：起 worker → 等 READY → 收尾。返回 (ok, msg, worker)。

        探测前先过冷却窗口：CTP 会话在 worker 退出后仍需一段时间才在
        柜台侧释放（崩溃时更是没有 Release，要等超时）——紧接着起新
        worker 会撞上旧会话并 SIGSEGV。
        """
        global _LAST_PROBE_END  # noqa: PLW0603 - 进程内节流标记
        import multiprocessing
        import queue as _queue
        import time

        wait = _PROBE_COOLDOWN - (time.monotonic() - _LAST_PROBE_END)
        if wait > 0:
            await asyncio.sleep(wait)

        async with _session() as session:
            row = await session.get(TradeAccount, account_id)
            if row is None:
                raise KeyError(f"账号不存在: {account_id}")
            config = {
                "broker_id": row.broker_id,
                "user_id": row.user_id,
                "password": decrypt(row.encrypted_password),
                "app_id": row.app_id,
                "auth_code": decrypt(row.encrypted_auth_code) if row.encrypted_auth_code else "",
                "md_front": row.md_front,
                "trade_front": row.trade_front,
            }

        ok = False
        msg = ""
        runner = self._worker_runner or _default_runner
        # 用真 CtpConfig——与生产同构（自造 duck-typed config 曾导致卡死）
        from ..infrastructure.ctp.adapters import CtpConfig  # noqa: PLC0415

        ctp_config = CtpConfig(**config)
        ctx = multiprocessing.get_context("spawn")
        q: Any = ctx.Queue()
        cmd_q: Any = ctx.Queue()
        worker = ctx.Process(
            target=runner,
            args=(q, cmd_q, ctp_config, [], _TEST_LIFETIME, "td"),
            name="ctp-account-probe",
            daemon=True,
        )
        worker.start()
        try:
            deadline = time.monotonic() + _TEST_TIMEOUT
            while time.monotonic() < deadline and worker.is_alive():
                try:
                    item = q.get(timeout=0.2)
                except _queue.Empty:
                    continue
                if isinstance(item, dict) and item.get("type") == "ready":
                    ok = True
                    msg = "认证/登录/结算单确认全部通过"
                    break
            if not ok and not worker.is_alive():
                msg = f"worker 提前退出（exitcode={worker.exitcode}）——凭证或会话未释放"
            elif not ok:
                msg = f"超时（{_TEST_TIMEOUT}s 未就绪）——柜台无响应或凭证错误"
        finally:
            if worker.is_alive():
                worker.terminate()
                # Release() 需要网络往返——给足时间，否则会话非优雅关闭
                worker.join(timeout=15)
            else:
                worker.join(timeout=1)  # 回收僵尸
            _LAST_PROBE_END = time.monotonic()
        return ok, msg, worker


def _default_runner(*args: Any, **kwargs: Any) -> None:
    """默认 worker 入口（延迟 import 防循环）。"""
    from ..infrastructure.ctp.worker import run_ctp_worker

    run_ctp_worker(*args, **kwargs)
