/**
 * 事件猎手栏目 E2E (连真实后端 8899 + 前端 5899)
 *
 * 覆盖:
 *  1. 页面加载: 标题/说明卡/统计卡
 *  2. 玩法体检区: 三块回测卡渲染 (listing 杠杆对照 / 深负费率 / 多周期共振)
 *  3. 候选机会: 新增 -> 列表出现 -> 状态流转(已触发) -> 删除 (API 清理, 不污染账本)
 *  4. 开仓账本: 空态提示存在
 */
import { expect, test, type Page } from "@playwright/test";

const TEST_INST = "E2E-TEST-USDT-SWAP";

/** 新增机会并提交 */
async function addOpportunity(page: Page) {
  await page.getByText("新增机会", { exact: false }).first().click();
  await page.waitForTimeout(400);
  await page.locator('input[placeholder="TRUMP-USDT-SWAP / BTC"]').fill(TEST_INST);
  await page.getByText("加入观察清单", { exact: false }).first().click();
  await page.waitForTimeout(800);
}

test.describe("事件猎手栏目 E2E", () => {
  test("页面加载: 标题 + 玩法说明 + 统计卡", async ({ page }) => {
    await page.goto("/workbench/hunter");
    await expect(page.getByText("事件猎手", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("五类机会", { exact: false })).toBeVisible();
    // 统计卡 (观察中/总开仓 等) — 页面可能有多条真实 watching 机会, 取首个
    await expect(page.getByText("观察中", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("累计盈亏", { exact: false }).first()).toBeVisible();
  });

  test("玩法体检区: 三块回测卡渲染 + 结论", async ({ page }) => {
    await page.goto("/workbench/hunter");
    await expect(page.getByText("玩法体检", { exact: false }).first()).toBeVisible({ timeout: 15_000 });
    // listing 杠杆对照
    await expect(page.getByText("上新首日 · 100x 彩票仓杠杆对照", { exact: false })).toBeVisible();
    await expect(page.getByText("玩法默认", { exact: false })).toBeVisible();
    await expect(page.getByText("抛硬币", { exact: false })).toBeVisible();
    // 深负费率
    await expect(page.getByText("深负费率", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("下跌动量延续", { exact: false }).first()).toBeVisible();
    // 多周期共振
    await expect(page.getByText("多周期 KDJ+MACD 共振", { exact: false })).toBeVisible();
    await expect(page.getByText("滞后顺势", { exact: false }).first()).toBeVisible();
    // 生成时间
    await expect(page.getByText("生成于", { exact: false }).first()).toBeVisible();
  });

  test("候选机会: 新增 -> 状态流转 -> 删除 (全程 API 清理)", async ({ page }) => {
    await page.goto("/workbench/hunter");
    // 从零开始: 若历史残留同名测试数据先删掉 (容错)
    await page.evaluate(async (inst) => {
      const r = await fetch("/api/hunter", { cache: "no-store" });
      const d = await r.json();
      for (const o of d.opportunities ?? []) {
        if (o.inst === inst) {
          await fetch(`/api/hunter/opportunities/${o.id}`, { method: "DELETE" });
        }
      }
    }, TEST_INST);
    await page.reload();
    await page.waitForTimeout(500);

    await addOpportunity(page);
    // 列表出现 + 观察中徽标
    await expect(page.getByText(TEST_INST).first()).toBeVisible();
    await expect(page.getByText("观察中", { exact: true }).first()).toBeVisible();
    // 状态流转 -> 已触发
    await page.getByText("已触发", { exact: false }).first().click();
    await page.waitForTimeout(800);
    // 删除 (confirm 自动接受)
    page.on("dialog", (d) => d.accept());
    await page.locator('button[title="删除"]').first().click();
    await page.waitForTimeout(800);
    await expect(page.getByText(TEST_INST)).toHaveCount(0);
  });

  test("开仓账本: 空态/记录区存在", async ({ page }) => {
    await page.goto("/workbench/hunter");
    await expect(page.getByText("开仓账本", { exact: false }).first()).toBeVisible();
  });
});
