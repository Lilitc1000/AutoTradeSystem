# AutoTradeSystem · 项目说明

期货自动交易系统：**多品种 · 多策略 · 回测实盘同构 · 无人值守全自动**。

架构基线：DDD + port-adapter（复刻 doc_process_studio 分层约定）。
- 用户说明书：`docs/user_manual.html`
- 技术设计文档：`docs/design/technical_design.html`

## 当前进度：M0-b 已完成（commit 见 git log）

- **M0-a**：领域模型（五聚合根 + Signal）+ 领域服务（PositionSizer 四层取小 /
  RiskEngine 硬规则 fail-closed）+ 应用层四端口 + BacktestBroker 最小回测闭环。
- **M0-b**：PG 持久层（14 张表 ORM + alembic 0001 初始迁移）+ 4 进程骨架
  （ProcessBase：信号处理/优雅退出/Redis 心跳）+ api_dashboard /healthz。

**质量数据**：115 项测试通过（含 5 项真实 PG 集成测试，db marker）；
ruff + mypy(strict) 零问题；容器内 `alembic upgrade head` 成功，
4 进程起停/优雅退出/心跳/healthz 全链路验证通过。

## 开发环境：devcontainer（复刻 doc_process_studio 模式）

三服务：**app**（开发容器，/workspace 挂载本仓库）+ **db**（postgres:16-alpine）
+ **redis**（redis:7-alpine）。app 容器 SSH 端口 **2223**（与 doc_process_studio
的 2222 错开，两个项目可并行运行）。

### 一次性启动（WSL 终端）

```bash
cd /home/autotoll/repos/AutoTradeSystem
python3 scripts/ensure_env.py --env dev    # 生成 .devcontainer/.env 密钥
docker compose -f .devcontainer/docker-compose.devcontainer.yml up -d --build
```

### 日常开发：ats.py（devbox 的 AutoTradeSystem 版）

Windows 侧通过 SSH 2223 操作容器，容器内 /workspace 的修改直接落在
WSL 文件系统上。**注意：Git Bash 会静默把 `/workspace` 这类参数转换成
Windows 路径导致 SFTP ENOENT，调用前必须 `export MSYS_NO_PATHCONV=1`**
（或路径用 `//workspace` 双斜杠写法）。

```bash
export MSYS_NO_PATHCONV=1
PY="C:/Users/autotoll/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
ATS="C:/Users/autotoll/.workbuddy/bin/ats.py"   # 主副本；repo scripts/ats.py 为同步副本

"$PY" "$ATS" exec "cd backend && PYTHONPATH=src python3 -m pytest tests -q"   # 跑测试
"$PY" "$ATS" exec "cd backend && PYTHONPATH=src python3 scripts/run_minimal_backtest.py"
"$PY" "$ATS" exec "cd backend && alembic upgrade head"                        # 数据库迁移
"$PY" "$ATS" exec "cd backend && PYTHONPATH=src python3 -m futures_trading.processes.md_gateway"  # 起进程
"$PY" "$ATS" ls /workspace/backend               # 列目录
"$PY" "$ATS" cat /workspace/backend/src/x.py     # 读文件
"$PY" "$ATS" put 本地文件.py backend/src/远端.py  # 覆盖上传（绕开 UNC 限制）
"$PY" "$ATS" sync                                # 增量镜像源码到本地 ats-mirror
```

### 容器内服务地址

| 服务 | 容器内地址 | 宿主（WSL）地址 |
|---|---|---|
| PostgreSQL | `db:5432`（用户/库 `autotrade`，密码见 `.devcontainer/.env`） | `127.0.0.1:5432` |
| Redis | `redis:6379`（密码见 `.devcontainer/.env`） | `127.0.0.1:6379` |
| api_dashboard（M2+） | `app:8000` | `127.0.0.1:8000` |

### 踩坑记录（2026-10-16）

1. **Git Bash MSYS 路径转换**：`ats.py ls /workspace` 的 `/workspace` 被转换成
   Windows 路径 → SFTP 服务器返回 ENOENT。修复：`MSYS_NO_PATHCONV=1`。
2. **SFTP 用 internal-sftp**：外部 sftp-server 在本环境不稳定，startup.sh
   已固定 `Subsystem sftp internal-sftp`。
3. **UNC 写限制**：WSL 9p 下不能覆盖/删除已存在文件（WorkBuddy 安全层同时
   拦截删除）——devcontainer 方案的根本价值就在于绕开这一切。
4. **startup.sh 不能用 `set -e`**：PID 1 下任何命令失败会杀死整个容器
   （doc_process_studio 的教训）。
5. **文件执行位（mode bit）**：`chmod +x` 过的脚本在 WSL/容器侧 git 会显示
   modified（100644 → 100755），Windows 侧因 core.fileMode=false 看不见——
   本仓库脚本统一 644（均由 bash/python 显式调用）；每次改动后以 **WSL 侧
   git status** 为准。
6. **sshd 不继承 compose environment**：SSH 会话里没有 DATABASE_URL——
   ats.py 的 exec 已自动 source `.devcontainer/.env` 并按 compose 同款规则
   重组装 DATABASE_URL / REDIS_URL。
7. **容器内装依赖**：`sudo uv pip install --system -e . --group dev`
   （`.[dev]` 语法 pip/uv 都不认 dependency-groups；系统 Python 需 sudo）。
8. **async lazy load**：AsyncSession 访问 relationship 会抛 MissingGreenlet，
   查询必须 eager load；集合用 selectinload（joinedload 一对多要求 .unique()）。
9. **容器内 redis-cli/psql**：服务在独立容器，要显式 `-h redis` / `-h db`；
   psql 不认 `postgresql+asyncpg://` 前缀，用 `${DATABASE_URL/+asyncpg/}` 转换。

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

## 下一步：M1（回测系统）

BacktestMarketData + BacktestBroker 事件驱动回测 + 历史数据导入（akshare）
+ 两级报告（实例级/组合级）+ 参数扫描。领域层与 M0-a 的 BacktestBroker 对接，
Strategy Engine 接正式回测循环。
