"""RiskEngine 领域服务：下单前硬规则终检（fail-closed）。

设计文档 6.1：所有规则不可关闭；风控引擎自身异常、行情状态未知、
账户查询失败——默认拒绝开新仓。宁可错过，不可错做。

规则按优先级排列，第一个命中的规则即返回（规则编号见 reason）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ....common.types import ZERO, Money


@dataclass(frozen=True)
class RiskLimits:
    """风控阈值（软参数，可配置；但规则本身不可关闭）。"""

    daily_loss_limit_pct: Decimal = Decimal("0.02")  # 单日亏损限额（占切片）
    slice_drawdown_limit_pct: Decimal = Decimal("0.15")  # 实例回撤熔断线
    portfolio_drawdown_limit_pct: Decimal = Decimal("0.10")  # 组合回撤熔断线


@dataclass(frozen=True)
class RiskSnapshot:
    """风控检查所需的账户/组合/系统状态快照。

    任何状态未知都必须由调用方填 False（fail-closed 的具体落地）。
    """

    equity: Money  # 组合权益
    peak_equity: Money  # 组合峰值权益
    slice_equity: Money  # 实例切片当前权益
    slice_peak: Money  # 实例切片峰值
    daily_pnl: Money  # 实例当日盈亏（负为亏损）
    instruments_reconciled: bool  # 本地与柜台持仓是否已对账一致
    market_data_fresh: bool  # 行情是否在超时内
    api_healthy: bool  # 交易 API 是否健康
    margin_sufficient: bool  # 保证金是否充足
    order_has_stop_loss: bool  # 开仓单是否同步带出止损单


@dataclass(frozen=True)
class RiskDecision:
    """风控裁决结果。"""

    allowed: bool
    rule: str | None  # 命中的规则编号；allowed=True 时为 None
    reason: str | None


_RECONCILE = "R01_RECONCILE_PENDING"
_STALE_MD = "R02_STALE_MARKET_DATA"
_API = "R03_API_UNHEALTHY"
_MARGIN = "R04_MARGIN_INSUFFICIENT"
_PORTFOLIO_DD = "R05_PORTFOLIO_DRAWDOWN"
_SLICE_DD = "R06_SLICE_DRAWDOWN"
_DAILY_LOSS = "R07_DAILY_LOSS_LIMIT"
_NO_STOP = "R08_NO_STOP_LOSS"


class RiskEngine:
    """下单前终检。纯策略对象，无外部依赖。"""

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self._limits = limits or RiskLimits()

    def check(self, snapshot: RiskSnapshot, *, is_open: bool) -> RiskDecision:
        """检查是否允许发出订单。

        is_open=True 表示开仓（受更多规则约束）；
        is_open=False 表示平仓（只受系统级规则约束，
        止损/熔断状态下的平仓永远放行）。
        """
        # 系统级规则（平仓同样受约束——状态不明时连平仓都不允许）
        if not snapshot.instruments_reconciled:
            return RiskDecision(False, _RECONCILE, "本地持仓与柜台不一致，禁止交易")
        if not snapshot.market_data_fresh:
            return RiskDecision(False, _STALE_MD, "行情停滞，禁止交易")
        if not snapshot.api_healthy:
            return RiskDecision(False, _API, "交易 API 不健康，禁止交易")
        if not snapshot.margin_sufficient:
            return RiskDecision(False, _MARGIN, "保证金不足，禁止交易")

        portfolio_dd = self._drawdown(snapshot.peak_equity, snapshot.equity)
        if portfolio_dd >= self._limits.portfolio_drawdown_limit_pct:
            return RiskDecision(False, _PORTFOLIO_DD, f"组合回撤 {portfolio_dd:.1%} 触及熔断线")

        slice_dd = self._drawdown(snapshot.slice_peak, snapshot.slice_equity)
        if slice_dd >= self._limits.slice_drawdown_limit_pct:
            return RiskDecision(False, _SLICE_DD, f"实例回撤 {slice_dd:.1%} 触及熔断线")

        if is_open:
            # 开仓专有规则
            daily_limit = self._limits.daily_loss_limit_pct
            if Decimal(snapshot.slice_peak) > ZERO:
                daily_pnl_ratio = Decimal(snapshot.daily_pnl) / Decimal(snapshot.slice_peak)
            else:
                daily_pnl_ratio = ZERO
            if daily_pnl_ratio <= -daily_limit:
                return RiskDecision(False, _DAILY_LOSS, f"当日亏损 {daily_pnl_ratio:.1%} 触及限额，停止开新仓")
            if not snapshot.order_has_stop_loss:
                return RiskDecision(False, _NO_STOP, "开仓单未同步带出止损单，拒绝")

        return RiskDecision(True, None, None)

    @staticmethod
    def _drawdown(peak: Money, current: Money) -> Decimal:
        peak_value = Decimal(peak)
        if peak_value <= ZERO:
            return ZERO
        drawdown = (peak_value - Decimal(current)) / peak_value
        return drawdown if drawdown > ZERO else ZERO
