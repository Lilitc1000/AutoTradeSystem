"""交易账号 API（M3-e1）：CRUD + 激活 + 连接测试。

安全：列表/详情**永不返回密文或明文密码**（_to_view 脱敏）；
连接测试走一次性 td worker（与生产同路径）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..application.account_service import AccountService

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

_service: AccountService = AccountService()


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    broker_id: str = Field(default="9999", max_length=16)
    user_id: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, description="交易密码（加密存储，不回显）")
    auth_code: str = Field(default="", description="看穿式 auth code（可空）")
    app_id: str = Field(default="simnow_client_test", max_length=32)
    md_front: str = Field(min_length=1, max_length=128)
    trade_front: str = Field(min_length=1, max_length=128)


class AccountPatch(BaseModel):
    name: str | None = None
    broker_id: str | None = None
    user_id: str | None = None
    password: str | None = None
    auth_code: str | None = None
    app_id: str | None = None
    md_front: str | None = None
    trade_front: str | None = None


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.get("")
async def list_accounts() -> dict[str, Any]:
    return {"accounts": await _service.list_accounts()}


@router.post("")
async def create_account(payload: AccountIn) -> dict[str, Any]:
    created: dict[str, Any] = await _service.create_account(payload.model_dump())
    return created


@router.put("/{account_id}")
async def update_account(account_id: int, payload: AccountPatch) -> dict[str, Any]:
    try:
        updated: dict[str, Any] = await _service.update_account(account_id, payload.model_dump(exclude_unset=True))
        return updated
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.delete("/{account_id}")
async def delete_account(account_id: int) -> dict[str, Any]:
    try:
        await _service.delete_account(account_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    return {"deleted": account_id}


@router.post("/{account_id}/activate")
async def activate_account(account_id: int) -> dict[str, Any]:
    try:
        activated: dict[str, Any] = await _service.activate(account_id)
        return activated
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.post("/{account_id}/test")
async def test_account(account_id: int) -> dict[str, Any]:
    """连接测试（同步等待 worker 就绪，约 5-25 秒）。"""
    try:
        result: dict[str, Any] = await _service.test_connection(account_id)
        return result
    except KeyError as exc:
        raise _not_found(exc) from exc
