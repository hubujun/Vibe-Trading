# Vibe-Trading 项目上下文 (CONTEXT.md)

> 维护者：Architect | 最后更新：2026-07-25

## 项目概述

基于 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) 的量化交易研究平台二次开发。自然语言驱动的 AI 金融研究 agent + 回测系统。重点开发 OKX broker connector、交易策略引擎、多 Agent 协作框架。

- **上游**：HKUDS/Vibe-Trading v0.1.13
- **Fork**：hubujun/Vibe-Trading
- **技术栈**：Python 3.11+ / FastAPI (8899) / Vue 3 + Vite (5899) / Docker Compose
- **测试**：pytest（`agent/tests/`）

## 架构快照

```
Vibe-Trading/
├── agent/                     # 后端 Python
│   ├── api_server.py          # FastAPI 入口
│   └── src/
│       ├── api/               # HTTP 路由层
│       │   └── live_routes.py # 运行时状态端点 (/live/status)
│       ├── trading/           # 交易层（经纪人连接器）
│       │   ├── service.py     # check_connection(), place_order(), ...
│       │   ├── profiles.py    # 配置文件注册中心
│       │   ├── types.py       # TradingProfile, Environment, Transport
│       │   └── connectors/
│       │       ├── okx/       # OKX broker (python-okx SDK)
│       │       ├── longbridge/# Longbridge broker
│       │       ├── ibkr/      # Interactive Brokers (local_tws)
│       │       └── ...        # alpaca, binance, futu, tiger, ...
│       ├── live/              # 实盘交易模块
│       └── config/            # 配置加载
├── frontend/                  # Vue 3 + Vite 前端
├── docs/
│   ├── adr/                   # 架构决策记录
│   │   ├── adr-001-okx-checkstatus-credential-source-error-branch.md
│   │   ├── adr-002-crypto-onchain-data-source.md
│   │   └── adr-003-oi-data-source-constraint.md
│   ├── interfaces/            # 接口规范文档
│   │   ├── onchain-data-loader.md
│   │   └── registry-symbol-whitelist.md
│   └── domain/                # 领域模型文档
│       └── crypto-onchain-factors.md
```

## 领域术语表 (Ubiquitous Language)

### 经纪人连接器域 (Broker Connector Domain)

| 术语 | 定义 | 示例 |
|------|------|------|
| **Profile** | 用户选择的连接器配置文件，封装了环境、传输方式、能力和只读标志 | `okx-paper-sdk`, `okx-live-sdk-readonly` |
| **Connector** | 经纪人标识键，对应一个 SDK 模块 | `okx`, `longbridge`, `binance` |
| **Environment** | 账户环境：paper（模拟）或 live（实盘） | `paper`, `live` |
| **Transport** | 连接器通信方式 | `broker_sdk`（本地 SDK）, `remote_mcp`（远程 MCP）, `local_tws`（IBKR 本地） |
| **Capabilities** | 配置文件暴露的能力集 | `account.read`, `positions.read`, `orders.place` |
| **Readonly** | 配置文件是否结构性地禁止写操作 | `true` / `false` |

### OKX 配置子域 (OKX Config Sub-domain)

| 术语 | 定义 | 取值 | 来源 |
|------|------|------|------|
| **profile** | 用户选择的风险姿态 | `paper` / `live-readonly` / `live` | `~/.vibe-trading/okx.json` |
| **environment** | 从 profile 派生的账户环境 | `paper` / `live` | `OKXConfig.environment` (property) |
| **flag** | OKX SDK 的模拟/实盘开关 | `"1"` (demo) / `"0"` (live) | `OKXConfig.flag` (property) |
| **credential_source** | 凭证来源标签（非凭证内容） | `"runtime_file"` / `"environment"` / `null` | `check_status()` 报告 |
| **configured** | 必需字段是否已填写完整 | `bool` | `check_status()` 报告 |
| **connection_state** | 当前连接状态 | `connected` / `not_configured` / `error` | `check_status()` 报告 |
| **error_code** | 结构化错误码 | `credentials_missing` / `sdk_missing` / `network_unreachable` / `authentication_failed` / `broker_error` | `check_status()` 报告 |

**推导链**：`profile → environment → flag`（单向，不可逆）

- `profile = "paper"` → `environment = "paper"` → `flag = "1"`（demo 环境，`x-simulated-trading: 1` header）
- `profile = "live-readonly"` → `environment = "live"` → `flag = "0"`（实盘环境，只读）
- `profile = "live"` → `environment = "live"` → `flag = "0"`（实盘环境，可交易）

**安全守卫**：`paper_guard = "header_flag+uid_pin"`——flag 控制 header 层面的环境选择，`expected_uid` 提供可选的应用层面二次校验。

### 状态报告域 (Status Report Domain)

| 术语 | 定义 | 边界 |
|------|------|------|
| **check_status()** | SDK 层的健康检查函数，返回 JSON 可序列化报告 | 不修改经纪人状态 |
| **check_connection()** | 服务层的连接检查，委托给 profile 对应的 SDK | `service.py:40` |
| **BrokerAuthState** | API 层的经纪人认证状态快照 | `live_routes.py:95`，所有字段经过白名单验证 |
| **_closed_vocabulary()** | 安全边界函数，只允许白名单值通过 | `live_routes.py:249` |

## 上游同步机制

上游 HKUDS/Vibe-Trading **会重写已发布历史**（对 `main` 强制推送）。2026-08-05 同步时，
`git fetch` 报告 upstream 独有 1187 个提交、本地"独有"1036 个——而本 fork 当时
**没有任何自己的提交**，那 1036 个全部是 SHA 被改写的上游提交。因此禁止用 merge/pull 同步。

分支职责严格切分：

| 分支 | 职责 |
|---|---|
| `main` | upstream/main 的**精确镜像**。禁止在此提交，因此永远可以安全 `reset --hard` |
| `dev/local-work` | 本地全部提交，每次同步 rebase 到镜像之上 |

**不变式**：`dev/local-work` 始终基于 `main` 的当前顶点。这是能用 `rebase --onto`
只重放本地提交的前提；直接 `git rebase main` 会退回 2026-04 的真实 merge base，
把上游自己改写过的提交重放一遍。

```bash
DRY_RUN=1 scripts/sync-upstream.sh   # 先看会发生什么
scripts/sync-upstream.sh             # 同步 + 重放本地提交
```

脚本会在 rebase 前打 `sync-backup/<时间戳>` 标签作为回滚点，并在工作区不干净或
不变式被破坏时拒绝执行。

### 已知本地环境问题

`frontend` 曾因 Node 25 有 31 个测试失败（`localStorage.clear is not a function`）：
Node 25 原生暴露了 `localStorage` 全局对象，遮蔽了 jsdom 的实现，而未提供
`--localstorage-file` 时其 `clear` 为 `undefined`。项目 `engines` 与 CI 均要求
Node 22。

**已解决**：Node 22.23.2 已安装到 `~/.local/node22`（官方二进制，独立于 brew；
brew 的 node@22 因 bottle tab 解析损坏暂不可用）。用它运行前端命令：

```bash
PATH="$HOME/.local/node22/bin:$PATH" npx vitest run   # 398 passed
PATH="$HOME/.local/node22/bin:$PATH" npx tsc --noEmit
```

`~/.local/node22` 不在 PATH 中，默认 shell 仍用 brew 的 Node 25；上述两行是
日常用法，也可自行把 PATH 前缀写进 `~/.zshrc`。

## 架构决策记录

| ADR | 标题 | 日期 |
|-----|------|------|
| [ADR-001](docs/adr/adr-001-okx-checkstatus-credential-source-error-branch.md) | OKX check_status credential_source and Error Branch Consistency | 2026-07-25 |
| [ADR-002](docs/adr/adr-002-crypto-onchain-data-source.md) | Crypto On-Chain Data Source for Alpha Factor Library | 2026-07-25 |
| [ADR-003](docs/adr/adr-003-oi-data-source-constraint.md) | OI Data Source Constraint — OKX Snapshot-Only & Historical Alternatives | 2026-07-25 |
| [ADR-004](docs/adr/adr-004-skill-agent-boundary-a-stock-data.md) | Skill-Agent Boundary — Remove a-stock-data from Architect | 2026-07-26 |

## 经纪人配置文件注册

所有经纪人通过 `TradingProfile` 在 `profiles.py` 中注册：

| 经纪人 | 配置文件 ID | 环境 | Transport |
|--------|------------|------|-----------|
| OKX | `okx-paper-sdk` | paper | broker_sdk |
| OKX | `okx-live-sdk-readonly` | live | broker_sdk |
| OKX | `okx-paper-trade` | paper | broker_sdk |
| OKX | `okx-live-trade` | live | broker_sdk |
| Longbridge | （多个） | paper/live | broker_sdk |
| IBKR | `ibkr-paper-local` | paper | local_tws |

## API 层安全约束

`live_routes.py` 在构建前端响应时对所有连接器报告字段进行白名单验证：

- `_CREDENTIAL_SOURCES` = `{"environment", "runtime_file"}` — 凭证来源标签
- `_CONNECTION_STATES` = `{"connected", "error", "not_configured", "ready"}` — 连接状态
- `_ERROR_CODES` = `{"authentication_failed", "broker_error", "credentials_missing", "credentials_partial", "network_unreachable", "sdk_missing"}` — 错误码
- `_ENVIRONMENT_IDENTITIES` = `{"config_declared", "header_flag+uid_pin", "host_separated", ...}` — 环境识别方法

所有 SDK 返回的字符串值必须在这白名单内，否则被 `_closed_vocabulary()` 过滤为 `None`。

## 当前任务状态

- **LAO-23**：架构反馈：OI数据源约束ADR + 链上指标有效域建模 + P0修复委派 → 当前任务

### 最近完成
- **LAO-17**：链上数据源调研 & 接入方案 ADR（MVRV/SOPR/交易所流量等）→ 已完成（ADR-002）

### 链上数据域 (On-Chain Data Domain)

| 术语 | 定义 | 示例 |
|------|------|------|
| **OnchainMetric** | 链上指标定义：唯一 ID、名称、分类、数据源列表 | `mvrv_zscore`, `sopr`, `exchange_netflow` |
| **OnchainPanel** | 链上指标 Panel 字典，key 格式 `onchain:{metric_id}` | `panel["onchain:mvrv"]` → DataFrame |
| **MVRV** | Market Value to Realized Value，市值/已实现市值 | >3.5 = 高估, <1.0 = 低估 |
| **SOPR** | Spent Output Profit Ratio，已花费输出利润率 | >1.05 = 获利了结, <1.0 = 亏损抛售 |
| **NVT** | Network Value to Transactions，市值/链上转账量 | "比特币的 PE Ratio" |
| **Dune Loader** | Dune Analytics REST API 加载器（Tier 1 主力源） | 免费，SQL 查询，~15min 延迟 |
| **Glassnode Loader** | Glassnode REST API 加载器（Tier 2 SOPR 补充源） | 免费 tier → $29/月 Pro |
| **The Graph Loader** | The Graph 去中心化索引加载器（Tier 3 长尾源） | 免费，GraphQL，需自建 subgraph |

### 链上指标有效域 (Domain of Validity for On-Chain Indicators)

链上指标（MVRV、SOPR、NVT、交易所净流量）的金融语义基于 **UTXO 价格模型**，该模型仅在 Bitcoin（及其直接分叉）上完整成立。跨截面应用于 altcoins（ETH、SOL、OKB 等）构成 **Category Error**，原因如下：

| 指标 | 原生链 | 有效域 | 跨截面应用于 Altcoin 的问题 |
|------|--------|--------|---------------------------|
| **MVRV** | Bitcoin | `BTC` | altcoins 无 UTXO 模型，`RealizedCap` 没有准确定义；Dune 上 altcoin MVRV 基于近似计算，不可靠 |
| **SOPR** | Bitcoin | `BTC` | SOPR 依赖 UTXO 级别 spent/output 聚合，EVM 链的账户模型不适用 |
| **NVT** | Bitcoin, Ethereum | `BTC`, `ETH` | ETH 有相对完整的转账量数据；其他 L1/L2 的 transfer value 计量口径不一致 |
| **交易所净流量** | 多链（按交易所钱包标签） | `BTC`, `ETH` | 需要可靠的交易所钱包标签系统；小币种的标签覆盖率低，流入/流出统计失真 |
| **活跃地址** | 多链 | `BTC`, `ETH` | 各链的"地址"概念不同（账户模型 vs UTXO），跨链比较无意义 |

**架构守卫**：`AlphaMeta.symbols_valid`（可选字段）—— 因子通过 `__alpha_meta__` 声明其适用的 symbol 白名单。`Registry.compute()` 在调用 `compute(panel)` 前检查：非白名单 symbol 列置为 NaN，防止 Category Error。

```python
# 链上因子标注示例
# crypto_mvrv_zscore.py
__alpha_meta__ = {
    ...
    "symbols_valid": ["BTC"],  # MVRV 仅在 BTC 上语义有效
}

# crypto_nvt_ratio.py
__alpha_meta__ = {
    ...
    "symbols_valid": ["BTC", "ETH"],  # NVT 在 BTC/ETH 上有可靠数据
}
```

### 数据源能力约束 (Data Source Constraints)

| 数据维度 | 源 | 能力 | 约束 |
|---------|------|------|------|
| OI 快照 | OKX `/open-interest` | 实时当前值 | 仅快照，无历史 |
| OI 历史（30天） | CCXT → OKX `/open-interest-history` | 近 30 天日频 OI | 30 天窗口限制 |
| OI 历史（全量） | CoinGlass ($149/月) | 2019 至今 | 付费，暂不接入 |
| 链上 BTC 指标 | Dune Analytics | MVRV/NVT/净流量/地址 | 免费，~15min 延迟 |
| 链上 BTC SOPR | Glassnode | SOPR | 免费 tier 有限，Pro $29/月 |
| 链上 altcoin 指标 | 无可靠免费源 | — | **架构级不可用**，见上方有效域 |
