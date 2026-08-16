# Vibe-Trading Backlog Roadmap Tickets（4 项待开发功能）

**日期**: 2026-08-10
**来源**: 代码盘点（Phase 0→4 路线图完成后的剩余 backlog）
**关联 Canvas**: `next-phase-backlog-roadmap.canvas.tsx`（待开发功能路线图）

---

## 依赖关系图

```
T1 策略提取 (无依赖)      T2 v2 WS 实盘流 (无依赖, 测试先行)
      \                     /
       \                   /
T3 payoff 分析 (无依赖, 底层现成)   T4 组合调仓执行 (建议在 T2 之后)
```

推荐执行顺序：**T1 → T3 → T2 → T4**（低风险闭环 → 纯增量 → 性能优化 → 资金链路收尾，paper-only）

---

## Ticket 1: 交易日志 Strategy 提取 + 回测桥接（Phase 4c）

**依赖**: 无
**文件**: `agent/src/tools/trade_journal_tool.py`, `agent/backtest/runner.py`, `agent/tests/test_trade_journal_tool.py`

### 背景
`analyze_trade_journal()` L472-476 中 `analysis_type="strategy"` 目前返回：
```python
result["strategy_features"] = {"status": "pending", "note": "Strategy extraction → backtest bridging lands in Phase 4c."}
```
`profile`（持仓/频率/胜率/时段分布）与 `behavior`（处置效应/过度交易/追涨/锚定）已实现。

### 实现内容
- 新增 `_compute_strategy(filtered_df)`：从交易记录提取可量化策略规则
  - 平均/中位持有期与最优持有期分桶
  - 进出场模式（按 symbol、market、时段、周内日聚合）
  - 仓位模式（加仓/减仓序列、单笔风险占比）
  - 输出结构化 JSON（rules / evidence / confidence）
- 可选桥接：规则生成后调用 backtest runner 做样本外验证，返回验证摘要
- 修正 `parameters` 中 `analysis_type` 描述（当前误写 "behavior/strategy are Phase 4b placeholders"，behavior 已实现）

### 验收标准
- `analysis_type="strategy"`（及 `"full"`）返回非 pending 的 `strategy_features`
- 新增单测覆盖：规则提取、空过滤集、桥接失败降级（不影响 profile/behavior 输出）

---

## Ticket 2: MarketFeed v2 WebSocket 实盘流

**依赖**: 无（测试已先行：`agent/tests/crypto_autopilot/test_market_feed_ws.py` 已就绪）
**文件**: `agent/src/crypto_autopilot/market_feed.py`, `agent/tests/crypto_autopilot/test_market_feed_ws.py`

### 背景
`MarketFeed` v1 用 REST 轮询（多交易对、增量缓存、限速保护）；`stream_bars()` 为 v2 占位。WS 测试文件已定义契约：channel 映射、candle 数组转换、confirmed-bar 过滤（`confirm=="1"`）、断线重连、订阅错误终止。

### 实现内容
- 实现 `stream_bars()`：OKX WebSocket 实盘 K 线流
  - `candle1m` 通道订阅（多 instId）
  - 9 字段 candle 数组 → 与 v1 一致的 DataFrame/记录格式
  - `confirm=="1"` 才标记已结算 bar（复用 `_is_bar_in_progress` 语义）
  - 断线自动重连（指数退避）、订阅错误显式终止
  - REST 轮询保留为回退路径（配置开关）
- 全部 WS I/O 以 mock 驱动，对齐既有测试契约

### 验收标准
- `test_market_feed_ws.py` 全绿（channel 映射/转换/确认过滤/重连/终止）
- v1 REST 路径回归通过；新增配置项切换 v1/v2

---

## Ticket 3: 期权 payoff 分析接入 API + UI

**依赖**: 无（`agent/backtest/options_payoff.py` 底层模块现成）
**文件**: `agent/src/api/options_lab_routes.py`, `agent/backtest/options_payoff.py`, `frontend/src/pages/OptionsLab.tsx`, `frontend/src/i18n/locales/*.json`, `agent/tests/test_options_lab_routes.py`

### 背景
`options_lab_routes` 仅有 2 个 GET（期权链 + IV 曲面）；`options_payoff.py` 的收益结构计算模块未被任何 API/UI 使用。

### 实现内容
- `options_lab_routes` 新增 payoff 端点（GET）：输入标的/到期/行权价/策略组合，复用 `options_payoff.py`
- 前端 `OptionsLab` 增加收益结构图（多策略叠加对比：买入看涨/备兑/跨式等）
- i18n 五语言补齐 payoff 相关文案

### 验收标准
- payoff 端点返回结构化收益数据（含盈亏平衡点/最大盈亏）
- 前端渲染多策略对比图；API 单测覆盖参数校验与边界

---

## Ticket 4: 组合调仓执行链路（paper 优先）

**依赖**: 建议在 T2 之后（实时价执行更稳）；无硬阻塞
**文件**: `agent/src/api/portfolio_routes.py`, `agent/src/crypto_autopilot/paper_engine.py`, `agent/src/crypto_autopilot/live_executor.py`, `agent/tests/test_portfolio_routes.py`

### 背景
`portfolio_routes` 现有 4 个分析型 POST（xray / rebalance_notes / constraints / optimize），全部只输出建议，无执行能力。

### 实现内容
- `optimize` 输出目标权重 → 与当前持仓计算差异对 → 调仓指令
- 调仓指令接入 `paper_engine` 模拟下单（沿用 $5/笔单笔上限、kill_loss_pct 风控）
- 新增只读 API：调仓计划预览 + 审批端点（默认 paper，实盘需显式参数确认）
- 全程复用 `live_executor` 的 scale/风控约束

### 验收标准
- optimize → paper 下单链路端到端测试通过（含差异对生成、风控拦截）
- 无显式确认时绝不触碰实盘；实盘路径保持用户显式确认红线
