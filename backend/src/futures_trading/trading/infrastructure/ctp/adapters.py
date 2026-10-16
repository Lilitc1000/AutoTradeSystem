"""CTP 适配器：三端口实现（M2-a 骨架，M2-b 联调）。

懒加载策略：openctp_ctp 是 C++ 扩展（仅 Linux wheel），
- 未安装/不可 import 时本模块仍可加载（适配器实例化才报错）
- Windows 开发机不装 openctp-ctp，mypy/ruff/pytest 不受影响

M2-b 联调前置（记录于 PROJECT.md）：
- SimNow 账号（broker_id/账号/密码，配置进 .devcontainer/.env）
- 容器 locale：openctp_ctp import 曾因 locale::facet abort，
  需 locales + localedef（当前容器已热修，Dockerfile 已声明）
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ....common.types import Money
from ...application.dtos import Bar, OrderCommand
from ...application.ports import AccountPort, MarketDataPort, OrderPort
from ...domain.model.instrument import Instrument
from ...domain.model.order import Order
from . import mapping

if TYPE_CHECKING:  # pragma: no cover
    pass


def _load_ctp() -> tuple[Any, Any]:
    """懒加载 CTP API（返回 (md_api, trade_api) 模块）。"""
    try:
        from openctp_ctp import thostmduserapi_se, thosttraderapi_se  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise RuntimeError(
            "openctp_ctp 未安装或不可加载（Linux C++ 扩展）。"
            "M2-b 联调前执行: pip install openctp-ctp 并确认容器 locale 正常"
        ) from exc
    return thostmduserapi_se, thosttraderapi_se


class CtpConfig:
    """CTP 连接配置（SimNow 参数走 settings/.env，M2-b 填入）。"""

    def __init__(
        self,
        broker_id: str = "9999",
        user_id: str = "",
        password: str = "",
        app_id: str = "simnow_client_test",
        auth_code: str = "0000000000000000",
        md_front: str = "tcp://180.168.146.187:10131",
        trade_front: str = "tcp://180.168.146.187:10130",
    ) -> None:
        self.broker_id = broker_id
        self.user_id = user_id
        self.password = password
        self.app_id = app_id
        self.auth_code = auth_code
        self.md_front = md_front
        self.trade_front = trade_front


class CtpMarketData(MarketDataPort):
    """CTP 行情适配器（M2-a 骨架）。

    M2-b 联调内容：CreateFtdcMdApi + spi 回调注册（OnFrontConnected/
    OnRtnDepthMarketData）→ BarAggregator → MessageBus.publish_bar。
    """

    def __init__(self, config: CtpConfig, bus: Any, instruments: dict[str, Instrument] | None = None) -> None:
        self.config = config
        self.bus = bus
        self.instruments = instruments or {}
        self._subscribed: list[str] = []

    def subscribe(self, symbols: Sequence[str]) -> None:
        missing = [s for s in symbols if s not in self.instruments]
        if missing:
            raise ValueError(f"品种未注册: {missing}")
        self._subscribed = list(symbols)

    def latest_bar(self, symbol: str, interval_minutes: int) -> Bar | None:
        raise NotImplementedError("CTP 行情经 MessageBus 推送，不提供同步查询（M2-b）")


class CtpTradeGateway(OrderPort):
    """CTP 报单适配器（M2-a 骨架）。

    M2-b 联调内容：ReqOrderInsert（mapping.command_to_ctp_input 填字段）+
    OnRtnOrder/OnRtnTrade 回报 → 更新域 Order + MessageBus.publish_fill。
    """

    def __init__(self, config: CtpConfig, bus: Any, instruments: dict[str, Instrument] | None = None) -> None:
        self.config = config
        self.bus = bus
        self.instruments = instruments or {}
        self._orders: dict[str, Order] = {}

    def submit(self, command: OrderCommand) -> Order:
        # M2-a：仅做映射验证与本地登记；M2-b 接 ReqOrderInsert
        _md, _trade = _load_ctp()
        ctp_input = mapping.command_to_ctp_input(command)
        contract = command.contract
        raise NotImplementedError(
            f"CTP 报单待 M2-b 联调（映射已验证: {contract} -> {len(ctp_input)} 字段）"
        )

    def cancel(self, order_id: str) -> Order:
        raise NotImplementedError("CTP 撤单待 M2-b 联调")

    def query(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)


class CtpAccount(AccountPort):
    """CTP 账户适配器（M2-a 骨架；M2-b 接 ReqQryTradingAccount/InvestorPosition）。"""

    def __init__(self, config: CtpConfig) -> None:
        self.config = config

    def balance(self) -> Money:
        raise NotImplementedError("CTP 账户查询待 M2-b 联调")

    def positions(self) -> list[Any]:
        raise NotImplementedError("CTP 持仓查询待 M2-b 联调")


__all__ = ["CtpConfig", "CtpMarketData", "CtpTradeGateway", "CtpAccount"]
