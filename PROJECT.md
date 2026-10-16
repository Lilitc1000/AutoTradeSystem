# AutoTradeSystem · 项目说明

期货自动交易系统：**多品种 · 多策略 · 回测实盘同构 · 无人值守全自动**。

架构基线：DDD + port-adapter（复刻 doc_process_studio 分层约定）。
- 用户说明书：`docs/user_manual.html`
- 技术设计文档：`docs/design/technical_design.html`

## 当前进度：M0-a 已完成（commit 27730a6）

领域模型（五聚合根 + Signal）+ 领域服务（PositionSizer 四层取小 /
RiskEngine 硬规则 fail-closed）+ 应用层四端口 + BacktestBroker 最小回测闭环。

**质量数据**：110 项单元测试通过；ruff + mypy(strict) 零问题；
domain 层覆盖率 92%（设计文档要求 ≥ 90%）。

## 开发

```bash
pip install -e "backend[dev]"
ruff check backend/                              # lint
mypy backend/src                                 # 类型检查
pytest backend/tests --cov=backend/src           # 测试 + 覆盖率
python backend/scripts/run_minimal_backtest.py   # 最小回测闭环（合成数据，无外部依赖）
```

## 结构

```
backend/src/futures_trading/
├── common/       # Price/Quantity/Money（Decimal）+ 通用错误
└── trading/
    ├── domain/
    │   ├── model/    # 聚合根：Instrument/Strategy/StrategyInstance/Order/Position/Portfolio
    │   └── service/  # PositionSizer（四层取小）/ RiskEngine（硬规则 fail-closed）
    ├── application/  # ports.py 端口定义 / dtos.py（Bar/OrderCommand）
    └── infrastructure/backtest/  # BacktestBroker 虚拟账户 + 模拟撮合 / SlippageModel
```

## 设计原则（详见设计文档第 1 章）

1. 单一 domain 核心，双端口适配——回测与实盘共用同一套策略/风控代码
2. 风控前置，fail-closed——违反不变量即拒绝，状态未知默认拒绝开仓
3. 策略与组合分层——StrategyInstance 只产意图，Portfolio 统一裁决净敞口
4. 显式状态优于隐式假设——订单状态机 + 重启强制对账，无单例全局状态
5. 可观测即默认——每个信号、每次风控拒绝全量落库

## 下一步：M0-b

PG 持久层（SQLAlchemy + alembic，12 张表）+ docker-compose + 4 进程空跑。
