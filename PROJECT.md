# AutoTradeSystem · 项目说明

期货自动交易系统：**多品种 · 多策略 · 回测实盘同构 · 无人值守全自动**。

架构基线：DDD + port-adapter（复刻 doc_process_studio 分层约定）。
- 用户说明书：`docs/user_manual.html`
- 技术设计文档：`docs/design/technical_design.html`

## 当前进度：M0-a 已完成（commit 27730a6）

领域模型（五聚合根 + Signal）+ 领域服务（PositionSizer 四层取小 /
RiskEngine 硬规则 fail-closed）+ 应用层四端口 + BacktestBroker 最小回测闭环。

**质量数据**：110 项单元测试通过；ruff + mypy(strict) 零问题；
domain 层覆盖率 92%（设计文档要求 ≥ 90%）。已在 devcontainer 内复跑验证
（Python 3.13.16，结果与本地一致）。

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

PG 持久层（SQLAlchemy + alembic，12 张表）+ 4 进程空跑。
（docker-compose devcontainer 已就绪：db/redis/app 三服务，SSH 2223）
