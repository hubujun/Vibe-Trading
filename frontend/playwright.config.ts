import { defineConfig } from "@playwright/test";

/**
 * Playwright E2E — 策略流水线栏目 (Workbench)
 * 依赖: 后端 8899 + 前端 5899 已运行 (launchd 托管), 连真实数据测试
 * 运行: cd frontend && npx playwright test
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false, // 共享同一后端数据, 串行避免互相干扰
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5899",
    headless: true,
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: "chromium", use: { browserName: "chromium", channel: "chrome" } }],
});
