# agent/src — 核心 Python 源码

## 模块边界

本目录是 Vibe-Trading 的核心后端逻辑，包含 862 个 Python 源文件，覆盖以下子包：

| 子包 | 职责 |
|------|------|
| `api/` | FastAPI 路由（alpha、portfolio、autopilot、options_lab 等） |
| `config/` | 环境配置、env schema、运行时参数 |
| `crypto_autopilot/` | 加密货币自动交易引擎（PaperEngine、orchestrator、market_feed） |
| `factors/` | Alpha 因子库（461+ 学术与基本面因子） |
| `agent/` | ReAct Agent 主循环（loop.py） |
| `tools/` | Agent 工具集（alpha_bench、trade_journal 等） |
| `skills/` | 项目级 SKILL.md 文件（89 个，含 alpha-zoo、akshare、chanlun 等） |
| `live/` | 实盘交易连接器（高风险面，需 broker 凭证） |
| `security/` | 安全守卫（订单门禁、只读默认、killswitch） |
| `memory/` | 记忆系统（Tier 1 质量评分 + Tier 2 语义检索） |
| `swarm/` | 多智能体协作 |

## 关键入口

- `agent/api_server.py` — FastAPI 服务入口
- `agent/mcp_server.py` — MCP 协议服务入口
- `agent/src/agent/loop.py` — ReAct Agent 主循环
- `agent/src/crypto_autopilot/cli_entry.py` — Autopilot CLI 入口

## 安全守卫

- `live/` 子包涉及实盘交易，变更必须通过订单安全测试
- `security/` 子包是安全边界，变更需额外审查
- `config/` 中的 env_schema 变更影响全局配置

## 聚焦测试命令

```bash
# 通用（覆盖大部分变更）
pytest --ignore=agent/tests/e2e_backtest --ignore=agent/tests/test_e2e_harness_v2.py --tb=short -q

# 订单安全相关变更（live/、security/、sdk_order、mandate）
pytest agent/tests/test_sdk_order_gate.py agent/tests/test_mandate_enforcement.py agent/tests/test_killswitch_blocks_orders.py agent/tests/test_readonly_default.py -q

# 因子变更（factors/）
pytest agent/tests/factors/test_alpha_purity.py agent/tests/factors/test_lookahead.py -q

# API 路由变更（api/）
pytest agent/tests/test_options_lab_routes.py agent/tests/test_portfolio_routes.py -q
```
