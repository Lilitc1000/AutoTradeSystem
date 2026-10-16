# AutoTradeSystem · 项目说明

期货自动交易系统：**多品种 · 多策略 · 回测实盘同构 · 无人值守全自动**。

架构基线：DDD + port-adapter（复刻 doc_process_studio 分层约定）。
- 用户说明书：`docs/user_manual.html`
- 技术设计文档：`docs/design/technical_design.html`

## 当前进度：M2-b 账号验证通过 + M2-c 环境固化（commit 见 git log）

- **M0-a**：领域模型（五聚合根 + Signal）+ 领域服务（PositionSizer 四层取小 /
  RiskEngine 硬规则 fail-closed）+ 应用层四端口 + BacktestBroker 最小回测闭环。
- **M0-b**：PG 持久层（14 张表 ORM + alembic 0001 初始迁移）+ 4 进程骨架
  （ProcessBase：信号处理/优雅退出/Redis 心跳）+ api_dashboard /healthz。
- **M1**：事件驱动回测引擎（BacktestClock/MarketData/Engine + SignalPipeline
  回测实盘共用）+ TrendATR 策略 + 两级指标（含 avg_loss_vs_budget 王牌指标）
  + Parquet 数据层 + akshare 导入 + 参数扫描（≤2 参数）+ 回测 API。
- **M2-a**：Redis 消息总线（md.bar/trade.order/signal.outcome）+ BarAggregator
  （tick→bar）+ CTP 适配器骨架（mapping 纯函数全覆盖单测；真连待 M2-b）+
  ReplayMarketData（Parquet 回放当实时行情）+ ShadowEngine（实盘链路无 IO，
  影子成交复用 BacktestBroker）+ strategy_core/md_gateway 接线。

**影子模式端到端已验证**（12 年 rb 日线回放 → 总线 → strategy_core：
120 个信号，EXECUTED/REJECTED(R07) 正常，权益 1,000,000→1,000,884）。

**M2-b 前置清单**（用户提供 SimNow 账号后）：
1. 注册 SimNow（simnow.com.cn，手机号当天生效）→ 账号/密码配 .devcontainer/.env
2. 容器装 `pip install openctp-ctp`（PyPI 包名；曾误试 `openctp` 不存在）
3. **CTP locale 问题**：openctp_ctp import 时 glibc C++ facet abort
   （locale::facet::_S_create_c_locale name not valid）。Dockerfile 已声明
   locales + LANG=en_US.UTF-8；当前容器已热修（apt locales + locale-gen）。
   联调时若仍 abort 需专项排查（localedef / glibc 版本）
4. CTP 适配器补真连：CreateFtdcMdApi/TraderApi + spi 回调 + 登录/订阅/报单
5. 影子 → SIM 切换：OrderPort 从 BacktestBroker 切 CtpTradeGateway

## M2-b/c 已完成（2026-10-16 晚）

**账号验证**：SimNow 账号 7x24 环境 CTP API 登录成功（BrokerID=9999，
TradingDay=20261015）。**迁址根因**：SimNow 2025-06-19 迁址，旧前置
180.168.146.187 停服；新前置：第一套(仿真) 182.254.243.31:30011/30001
（跟随交易时段）、7x24 40011/40001（16:00~次日09:00）。

**openctp Python 绑定根治（patch 方案）**：
- 根因（gdb+反汇编）：openctp_ctp SWIG 包装含
  `static std::locale g_loc("zh_CN.GB18030")`，glibc 无此 locale 定义
  （locales 包只到 GBK；LC_IDENTIFICATION codeset 校验无法满足）→
  import 即 abort。试遍 locales/charmap/localedef/ICU/Py3.11/官方 so 无效。
- 修复：二进制 patch `.so` 中 "zh_CN.GB18030" → "C"（等长 \0 填充），
  脚本 `backend/scripts/ctp_probe/patch_gbk.sh`（Dockerfile build 阶段执行）。
- 验证：patch 后 Python 绑定真实登录 SimNow 成功（py_login.py）。

**CTP 已知问题（技术债）**：
1. 中文字段乱码：patch 后 boost GBK→UTF8 退化为字节透传（品种名等中文乱码，
   ID/数字字段不受影响）。根治需 openctp 上游修复或源码编译时替换 .i 中 locale 名。
2. md+td 同时 import 的进程退出时可能 segfault（atexit 析构）；功能不受影响
   （登录/行情/报单路径正常），verify_env 以输出判定容忍。
3. 升级 openctp-ctp 版本必须重验 patch 匹配（字符串位置/内容可能变）。

**环境固化（M2-c，保证容器重建不丢）**：
- Dockerfile：locales + openctp-ctp==6.7.11.0 + patch + **全部第三方依赖**
  （sqlalchemy/pandas/pyarrow/fastapi/pytest 等，版本对齐 pyproject）
- startup.sh：仅做秒级 editable 注册（`pip3 install --no-deps -e .`）——
  9p 挂载卷上 uv/pip 全量安装曾卡 10 分钟+，故依赖不进 startup
- compose：build context 改 repo 根（Dockerfile COPY 脚本需要）+
  repo 根 .dockerignore（排除 .git/data/__pycache__）
- **重建流程**：`docker compose -f .devcontainer/docker-compose.devcontainer.yml
  build app && up -d` → `docker exec devcontainer-app-1 bash
  /workspace/backend/scripts/verify_env.sh`（5 项一键验证：CTP import /
  alembic / pytest / SimNow 登录 / 回放影子链路）
- 凭证在 .devcontainer/.env（gitignore）：SIMNOW_USER_ID/PASSWORD/APP_ID/
  AUTH_CODE + CTP_BROKER_ID/MD_FRONT(40011)/TRADE_FRONT(40001)

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
10. **openctp 安装**：PyPI 包名是 `openctp-ctp`（`openctp` 不存在），模块名
    `openctp_ctp`；import 需容器 locale（见 M2-b 前置清单第 3 条）。
11. **函数内相对 import 逃过静态检查**：mypy/import 诊断只覆盖模块级，
    进程启动才炸（strategy_core 曾中招）——相对 import 一律写模块顶部。
12. **ats.py exec + 后台进程**：`setsid ... &` 与前台命令混排时 SSH channel
    读取会挂起（bash 非交互等 job）——起进程与跑任务分两次 exec；
    后台进程必须 `< /dev/null > log 2>&1` 全重定向。

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

## 下一步：M2-d（CTP 适配器真连）+ M3（前端）

- M2-d：CtpMarketData/CtpTradeGateway 补 spi 回调真连（登录已验证，
  剩订阅/报单/回报/对账）；影子 → SIM 切换；tick→bar 聚合接总线
- M3：前端 dashboard（回测报告/曲线/持仓/信号）+ settings 页
  （SimNow 凭证从 .env 迁入 system_secrets 加密存储，复刻 DPS 模式）
