"""凭证加密测试（M3-e1）：Fernet roundtrip + 未配置密钥报错。"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from futures_trading.common import crypto


@pytest.fixture(autouse=True)
def _reset_fernet():
    crypto._fernet = None  # noqa: SLF001 - 单例缓存，用例间重置
    yield
    crypto._fernet = None  # noqa: SLF001


def test_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", key)
    ciphertext = crypto.encrypt("s3cr3t-P@ss")
    assert ciphertext != "s3cr3t-P@ss"
    assert crypto.decrypt(ciphertext) == "s3cr3t-P@ss"


def test_encrypt_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", "")
    with pytest.raises(RuntimeError, match="SETTINGS_ENCRYPTION_KEY"):
        crypto.encrypt("x")


def test_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", "")
    assert crypto.is_configured() is False
    monkeypatch.setattr(crypto.settings, "settings_encryption_key", Fernet.generate_key().decode())
    assert crypto.is_configured() is True
