# frontend/src — React 19 前端

## 模块边界

本目录是 Vibe-Trading 的前端应用，基于 Vite + React 19 + TypeScript，共 73 个源文件。

| 子目录 | 职责 |
|--------|------|
| `pages/` | 页面组件（Agent、AlphaZoo、Autopilot、OptionsLab、Portfolio 等） |
| `components/` | 可复用 UI 组件 |
| `lib/` | 工具库（api.ts — API 客户端、apiAuth.ts — 认证、echarts.ts — 图表） |
| `hooks/` | 自定义 React Hooks |
| `stores/` | 状态管理 |
| `i18n/` | 国际化（6 语言：en、zh-CN、ja、ko、ar、补充） |
| `types/` | TypeScript 类型定义 |
| `__tests__/` | Vitest 测试 |

## 关键约定

- API 客户端统一通过 `lib/api.ts`，不直接使用 fetch/axios
- 新增页面需在路由中注册，并在 i18n 中添加 6 语言翻译键
- Autopilot 页面与 `agent/src/crypto_autopilot/` 的 API 端点对接
- 使用 ECharts 做图表渲染，主题通过 `lib/chart-theme.ts` 统一管理
- API 部署超出 loopback 时需配置 `API_AUTH_KEY`（见 `lib/apiAuth.ts`）

## 聚焦测试命令

```bash
cd frontend && npx vitest run --reporter=verbose
```

## 注意事项

- Node 25 的原生 localStorage 会遮蔽 jsdom，导致 vitest 失败；使用 Node 22 运行前端测试
- i18n locale 文件变更时需同步所有 6 个语言文件
