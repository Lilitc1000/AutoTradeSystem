"""交易业务错误。"""

from __future__ import annotations

from ...common.errors import DomainError


class InvalidOrderTransitionError(DomainError):
    """订单状态机非法迁移（如从 FILLED 再 submit）。"""


class InvalidFillError(DomainError):
    """非法成交（成交量为负、超出订单剩余量等）。"""


class InsufficientPositionError(DomainError):
    """平仓量超过可用持仓。"""
