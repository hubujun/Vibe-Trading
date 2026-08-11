
---

# Vibe-Trading Squad 工作流规则

> 本文档位于 MULTICA-RUNTIME 区块之后，包含 Squad 级别的协作规范。
> 相关 ADR：`docs/adr/001-state-transition-ownership.md`

## 状态流转强制规则

### 状态机

```
todo ──→ in_progress ──→ in_review ──→ done
  ↑           │               │
  │           │               ├──→ in_progress (review changes requested)
  │           │               │
  └───────────┴───────────────┘

blocked   ←── 任意状态
backlog   ←── 仅 todo
cancelled ←── 任意状态
```

### 强制出口检查（每个 Agent 必须执行）

**每次回复前（尤其是工作完成后的回复）**，必须确认以下三点，不满足则不得发出评论：

1. ✅ 本次运行的成果是什么？
2. ✅ Issue 当前状态是否反映了这个成果？
3. ✅ 不匹配 → 先翻转状态，再回复（`multica issue status <id> <new_status>`）

### 状态翻转所有权表

| 谁 | 什么时候 | 翻成什么 |
|---|---------|---------|
| Developer | 实现完成并自测通过 | `in_progress` → `in_review` |
| Developer | 完成 Review 修改 | `in_progress` → `in_review` |
| Reviewer | 需要修改 | `in_review` → `in_progress` |
| Reviewer | 审查通过 | `in_review` → `done`（或保持等人工验收） |
| Architect | 架构把关通过 | `in_review` → `done` |
| Architect | 直接执行并产出最终结果（评估/调研/简单实现/用户确认后的后续执行） | `in_progress` → `in_review`（若需他人验收）或直接 → `done`（评估/调研类无需审查的任务） |
| 任何人 | 发现阻塞 | 当前状态 → `blocked` |

### 常见反模式

- ❌ "我改完了代码，但没法翻状态，因为 AGENTS.md 说不要随便改状态"
  → ✅ 改完代码**就应该翻状态**，这是交付的一部分，不是"随便改"
- ❌ "我以为 Reviewer 会翻状态"
  → ✅ 你完成的工作，你翻状态。明确所有权，不依赖他人
- ❌ 改完 Review 意见就回复了，没翻状态
  → ✅ **这就是 LAO-39 的根因**。修复代码后必须翻回 `in_review`
- ❌ "Architect 自己做完评估/实现，发了评论但没翻状态"
  → ✅ Architect 直接交付时，同样是工作完成者，强制出口检查规则完全适用。成果产出后必须翻状态。
- ❌ "我改完了代码但没跑测试，直接翻到 `in_review`"
  → ✅ 先运行聚焦测试并报告退出码（见「编辑后验证」），验证通过后才能翻转状态到 `in_review`

## 项目上下文路由

开始任务前，优先查阅以下文档获取项目上下文：

| 文档 | 用途 |
|------|------|
| [`CONTEXT.md`](CONTEXT.md) | 架构概览、领域术语表（Broker Connector Domain、OKX Config Sub-domain 等）、安全守卫 |
| [`AGENT_CONTRIBUTOR_GUIDE.md`](AGENT_CONTRIBUTOR_GUIDE.md) | 安全本地检查命令、高风险面（broker/MCP/credential）、按变更类型的定向测试提示、PR 规范 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 环境搭建快速入门（`pip install -e ".[dev]"`）、DCO 签名要求、Alpha Zoo 因子贡献审查清单 |

## 编辑后验证

代码变更完成后，必须运行与变更范围匹配的最小聚焦测试命令，并报告结果（包含退出码）。未运行的测试需说明原因。

### 强制检查点

**Agent 在声明任务完成前，必须执行以下步骤：**

1. **识别变更范围** — 根据本次编辑涉及的文件和模块，选择上面对应的最小聚焦测试命令。
2. **执行测试** — 运行选定的测试命令，记录完整命令、退出码和关键输出摘要。
3. **报告结果** — 在完成回复中包含以下信息：
   - 执行的测试命令（如 `pytest agent/tests/test_sdk_order_gate.py -q`）
   - 退出码（如 `exit code: 0`）
   - 结果摘要（如 `3 passed in 0.5s`）
4. **未运行测试时的处理** — 如果无法确定合适的测试命令，必须明确说明原因并获得用户确认后才可跳过验证。禁止在未运行测试且未说明原因的情况下声明任务完成。

### 强制出口检查（每次代码编辑后）

**每次代码编辑后（无论任务是否完成），在发出下一条回复前**，必须确认以下三点，不满足则不得继续：

1. ✅ 本次编辑涉及的最小聚焦测试命令是什么？（在下方命令库中选择）
2. ✅ 该命令是否已执行并记录了退出码？
3. ✅ 未执行 → 是否已说明原因并获得用户确认？

该检查与「状态流转强制规则」的强制出口检查并列执行：先验证、再翻状态、最后回复。**未运行聚焦测试且未报告退出码时，不得翻转状态到 `in_review`**（也不得声明任务完成）——验证执行是状态翻转的前置条件，禁止在未运行测试且未说明原因的情况下声明任务完成。

### Python 变更

```bash
# 通用（覆盖大部分后端变更）
pytest --ignore=agent/tests/e2e_backtest --ignore=agent/tests/test_e2e_harness_v2.py --tb=short -q

# 订单安全相关变更
pytest agent/tests/test_sdk_order_gate.py agent/tests/test_mandate_enforcement.py -q

# 因子变更
pytest agent/tests/factors/test_alpha_purity.py agent/tests/factors/test_lookahead.py -q
```

### 前端变更

```bash
cd frontend && npx vitest run --reporter=verbose
```

> 更多定向测试命令见 [`AGENT_CONTRIBUTOR_GUIDE.md`](AGENT_CONTRIBUTOR_GUIDE.md) 的 Targeted Test Hints 章节。

### Pre-commit Hook

项目已配置 pre-commit hook（`scripts/git-hooks/pre-commit`），通过 `scripts/setup-dev.sh`（或手动 `git config core.hooksPath scripts/git-hooks`）启用。提交代码时会自动根据变更文件类型运行聚焦测试：

- Python 变更 → 运行 `pytest --ignore=agent/tests/e2e_backtest --tb=short -q`
- 高风险变更（live/、security/、sdk_order、mandate）→ 运行订单安全测试门禁
- 前端变更 → 运行 `npx vitest run --reporter=dot`
- 跳过验证：`git commit --no-verify`（仅在紧急情况下使用）

## 受控启动

### 后端启动

```bash
# 安装依赖（开发模式）
pip install -e ".[dev]"

# 启动 API 服务
python agent/api_server.py

# 启动 MCP 服务
python agent/mcp_server.py

# 启动 Autopilot
python -m agent.src.crypto_autopilot.cli_entry
```

### 前端启动

```bash
cd frontend
npm ci
npm run dev    # 开发模式
npm run build  # 生产构建
```

### 验证启动状态

```bash
# 后端测试套件
pytest --ignore=agent/tests/e2e_backtest --ignore=agent/tests/test_e2e_harness_v2.py --tb=short -q

# 前端测试套件
cd frontend && npx vitest run --reporter=verbose

# CLI 语法检查
cd agent && python -m compileall -q cli
```

### Docker 启动

```bash
docker-compose up -d  # 使用 docker-compose.yml
```

## 交付验收

### 交付边界

代码变更按以下路径交付：

1. **本地验证** — 运行聚焦测试（见「编辑后验证」），确认退出码为 0
2. **Pre-commit** — 提交时自动运行聚焦测试（见「Pre-commit Hook」）
3. **CI** — push/PR 触发 `.github/workflows/test.yml`（Python 测试 + 前端构建 + Windows 兼容性 + 安全门禁）
4. **Merge** — CI 通过后合并到 `main`（`main` 是上游只读镜像，本地工作在 `dev/local-work` 分支）

### 高风险审批

以下变更需要额外审查才能交付：

- **Broker/订单** — `agent/src/live/`、`agent/src/security/` 中的变更需通过订单安全测试门禁
- **凭证/密钥** — 涉及 API key、broker credential 的变更需审查安全边界
- **MCP 服务** — 外部 MCP 服务器是操作信任面，变更需审查调用者权限
- **生产部署** — API/Web 部署超出 loopback 时需配置 `API_AUTH_KEY`

### 完成确认

任务完成前必须获得以下信号之一：

- 用户显式确认（如「好的」「可以」「合并吧」）
- CI 通过且 PR 已 merge
- 评估/调研类任务：用户确认收到结果

### 回滚路径

| 场景 | 回滚方式 |
|------|----------|
| 代码变更 | `git revert` 或 `git reset`（`dev/local-work` 分支） |
| 上游同步 | `sync-backup/*` tag（`scripts/sync-upstream.sh` 自动创建） |
| Autopilot 服务 | `launchctl unload` → 修复 → `launchctl load` |
| Docker 服务 | `docker-compose down` → 修复 → `docker-compose up -d` |
