"""凭证加密服务（M3-e）：Fernet 对称加密交易账号密码/auth code。

与 doc_process-studio 同款模式：密钥走环境变量（SETTINGS_ENCRYPTION_KEY），
密文落 DB（trade_accounts.encrypted_*）。密钥不在代码/仓库里——.env 注入。

未配置密钥时 encrypt/decrypt 抛异常（调用方决定回落：明文 .env 或拒绝保存）。
"""

from __future__ import annotations

from cryptography.fernet import Fernet

from .settings import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet  # noqa: PLW0603 - 进程内单例（Fernet 无状态，缓存省重复构造）
    if _fernet is None:
        key = settings.settings_encryption_key
        if not key:
            raise RuntimeError(
                "SETTINGS_ENCRYPTION_KEY 未配置——无法加密账号凭证。"
                "请在 .env 设置 Fernet 密钥（生成：python -c "
                "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'）"
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """明文 → Fernet 密文（str）。"""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Fernet 密文 → 明文。密钥错误/密文损坏抛 InvalidToken。"""
    return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


def is_configured() -> bool:
    """密钥是否已配置（前端可据此提示用户）。"""
    return bool(settings.settings_encryption_key)
