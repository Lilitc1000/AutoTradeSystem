"""交易账号服务测试（M3-e1）：CRUD + 脱敏 + 激活唯一 + 连接测试（Fake worker）。"""

from __future__ import annotations

import os
from typing import Any

import pytest
from cryptography.fernet import Fernet

from futures_trading.common import crypto
from futures_trading.trading.application import account_service as account_service_module
from futures_trading.trading.application.account_service import AccountService
from futures_trading.trading.infrastructure.persistence.database import Database

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch: pytest.MonkeyPatch):
    crypto._fernet = None  # noqa: SLF001
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", Fernet.generate_key().decode())
    yield
    crypto._fernet = None  # noqa: SLF001


@pytest.fixture
async def db():
    url = os.getenv("ATS_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("未设置 ATS_TEST_DATABASE_URL / DATABASE_URL")
    from sqlalchemy import text

    from futures_trading.trading.infrastructure.persistence.base import Base

    database = Database(url)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    async with database.engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'DELETE FROM "{table.name}"'))
    await database.dispose()


@pytest.fixture
def service(db: Database, monkeypatch: pytest.MonkeyPatch) -> AccountService:
    monkeypatch.setattr(account_service_module, "database", db)
    return AccountService()


class _FakeProcess:
    """线程模拟 multiprocessing.Process（spawn 无法 pickle 闭包 runner）。"""

    def __init__(self, target: Any = None, args: tuple[Any, ...] = (), name: str = "", daemon: bool = True) -> None:
        self.target = target
        self.args = args
        self.pid = 4242
        self.exitcode: int | None = 0
        self._thread: Any = None

    def start(self) -> None:
        import threading as _threading

        self._thread = _threading.Thread(target=self.target, args=self.args, daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def terminate(self) -> None:
        self.exitcode = -15

    def join(self, timeout: float | None = None) -> None:
        return


class _FakeCtx:
    def Queue(self) -> Any:
        import queue as _queue

        return _queue.Queue()

    def Process(self, **kwargs: Any) -> _FakeProcess:
        return _FakeProcess(**kwargs)


def _install_fake_mp(monkeypatch: pytest.MonkeyPatch) -> None:
    """spawn 上下文 → Fake（线程跑 target，绕开 pickle 限制）。"""
    import multiprocessing

    monkeypatch.setattr(multiprocessing, "get_context", lambda _name: _FakeCtx())


def _payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "name": "SimNow 主账号",
        "broker_id": "9999",
        "user_id": "123456",
        "password": "s3cr3t",
        "auth_code": "0000000000000000",
        "app_id": "simnow_client_test",
        "md_front": "tcp://182.254.243.31:30011",
        "trade_front": "tcp://182.254.243.31:30001",
    }
    base.update(overrides)
    return base


class TestAccountCrud:
    async def test_create_list_never_leaks_secrets(self, service: AccountService) -> None:
        view = await service.create_account(_payload())
        assert view["name"] == "SimNow 主账号"
        assert view["is_active"] is True  # 首个账号自动激活
        listed = await service.list_accounts()
        assert len(listed) == 1
        blob = repr(listed[0])
        assert "s3cr3t" not in blob
        assert "encrypted" not in blob  # 无密文字段

    async def test_activate_is_exclusive(self, service: AccountService) -> None:
        a = await service.create_account(_payload(user_id="111"))
        b = await service.create_account(_payload(user_id="222"))
        assert (await service.get_active_id()) == a["id"]
        await service.activate(b["id"])
        assert (await service.get_active_id()) == b["id"]
        listed = {row["id"]: row["is_active"] for row in await service.list_accounts()}
        assert listed == {a["id"]: False, b["id"]: True}

    async def test_update_password_reencrypts(self, service: AccountService) -> None:
        created = await service.create_account(_payload())
        await service.update_account(created["id"], {"password": "new-pass"})
        config = await service.active_config_dict()
        assert config is not None
        assert config["password"] == "new-pass"

    async def test_active_config_falls_back_none(self, service: AccountService) -> None:
        assert await service.active_config_dict() is None

    async def test_delete(self, service: AccountService) -> None:
        created = await service.create_account(_payload())
        await service.delete_account(created["id"])
        assert await service.list_accounts() == []


class TestConnectionProbe:
    async def test_ready_marks_ok(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fake worker：READY 到达 → 连接测试通过，结果落库。"""
        monkeypatch.setattr(account_service_module, "database", db)
        _install_fake_mp(monkeypatch)

        def fake_runner(  # noqa: PLR0913 - 对齐真 worker 签名
            q: Any, cmd_q: Any, config: Any, contracts: list[str], lifetime: Any, mode: str
        ) -> None:
            import queue as _queue

            # 模拟认证链成功（真 worker 的 READY 语义）
            q.put({"type": "ready", "front_id": 1, "session_id": 2})
            try:
                while True:
                    cmd_q.get(timeout=0.1)
            except _queue.Empty:
                return

        service = AccountService(worker_runner=fake_runner)
        created = await service.create_account(_payload())
        result = await service.test_connection(created["id"])
        assert result["ok"] is True
        listed = await service.list_accounts()
        assert listed[0]["last_test_ok"] is True
        assert listed[0]["last_test_msg"]

    async def test_worker_dies_marks_failed(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fake worker 直接退出（无 READY）→ 测试失败，带 exitcode 信息。"""
        monkeypatch.setattr(account_service_module, "database", db)
        _install_fake_mp(monkeypatch)

        def dead_runner(  # noqa: PLR0913 - 对齐真 worker 签名
            q: Any, cmd_q: Any, config: Any, contracts: list[str], lifetime: Any, mode: str
        ) -> None:
            return  # 模拟崩溃/凭证错误

        service = AccountService(worker_runner=dead_runner)
        created = await service.create_account(_payload())
        result = await service.test_connection(created["id"])
        assert result["ok"] is False
        assert "exitcode" in result["msg"]

    async def test_probe_worker_is_terminated(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        """常驻 Fake worker（不发 READY）→ 超时后必须被 terminate（不泄漏进程）。"""
        monkeypatch.setattr(account_service_module, "database", db)
        _install_fake_mp(monkeypatch)

        def slow_runner(  # noqa: PLR0913 - 对齐真 worker 签名
            q: Any, cmd_q: Any, config: Any, contracts: list[str], lifetime: Any, mode: str
        ) -> None:
            import time as _time

            _time.sleep(120)  # 模拟卡住的 worker

        service = AccountService(worker_runner=slow_runner)
        created = await service.create_account(_payload())
        # 缩小超时避免测试等 25 秒
        import futures_trading.trading.application.account_service as svc_module

        monkeypatch.setattr(svc_module, "_TEST_TIMEOUT", 2)
        result = await service.test_connection(created["id"])
        assert result["ok"] is False
        assert "超时" in result["msg"]
