"""通用错误基类。"""

from __future__ import annotations


class DomainError(Exception):
    """领域错误基类：所有业务规则违反都以领域错误向上冒泡。"""


class InvariantViolationError(DomainError):
    """聚合根不变量被违反（如资金切片非正、参数版本号非递增）。"""


class ValidationError(DomainError):
    """输入值校验失败。"""
