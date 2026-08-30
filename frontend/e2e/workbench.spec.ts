/**
 * 策略流水线栏目 E2E (连真实后端 8899 + 前端 5899)
 *
 * 覆盖:
 *  1. 页面加载与基本结构 (三卡布局: 当前阶段 + 左右相邻)
 *  2. 策略下拉切换联动
 *  3. 六阶段卡浏览 + 详情面板门控 (研究/组合/执行/复盘)
 *  4. 数据一致性 (2026-08-30 事故回归: 研究卡指标不得重复/僵尸显示 --)
 */
import { expect, test, type Page } from "@playwright/test";

/** 策略下拉 (首个 select) */
const comboBox = () => "select";

/** 阶段卡 h2 (含 Term ? 组件, 锚定开头) */
const stageCard = (page: Page, name: string) =>
  page.locator("h2", { hasText: new RegExp(`^${name}`) }).first();

/** 点击阶段卡 */
async function gotoStage(page: Page, name: string) {
  await stageCard(page, name).click();
  await page.waitForTimeout(300);
}

/** 从策略实际阶段逐级导航到目标 (先点"回当前"复位, 再按相邻卡移动) */
async function navigateTo(page: Page, target: string) {
  const backBtn = page.getByText("回当前", { exact: false });
  if (await backBtn.count()) {
    await backBtn.first().click();
    await page.waitForTimeout(300);
  }
  const order = ["挖掘", "组合", "研究", "模拟", "执行", "复盘"];
  let idx = order.indexOf("模拟"); // paper 策略实际阶段
  const targetIdx = order.indexOf(target);
  while (idx !== targetIdx) {
    if (idx < targetIdx) idx += 1;
    else idx -= 1;
    await gotoStage(page, order[idx]);
  }
}

/** 选中一条 paper 策略 (基策略), 确保浏览基线 = 模拟阶段 */
async function selectBaseStrategy(page: Page, labels: string[]) {
  const base = labels.find((l) => l.startsWith("BAB+high52w 双因子组合 —")) ?? labels[0];
  await page.locator(comboBox()).selectOption({ label: base });
  await page.waitForTimeout(500);
}

/** 研究卡指标: 读 label 对应的 value (font-mono) */
async function researchValue(page: Page, label: string): Promise<string> {
  // h2 的父是 header div, 卡容器需要 ancestor 上溯到 rounded-xl
  const card = stageCard(page, "研究").locator("xpath=ancestor::div[contains(@class,'rounded-xl')][1]");
  const item = card.locator("div.text-muted-foreground", { hasText: label }).first();
  const valueDiv = item.locator("xpath=following-sibling::div[contains(@class,'font-mono')]").first();
  return (await valueDiv.textContent())?.trim() ?? "";
}

async function optionLabels(page: Page): Promise<string[]> {
  return page.locator(`${comboBox()} option`).allTextContents();
}

/** 等待策略数据加载完成 (options 填充) */
async function waitForOptions(page: Page): Promise<string[]> {
  await expect.poll(async () => (await optionLabels(page)).length, { timeout: 20_000 }).toBeGreaterThanOrEqual(20);
  return optionLabels(page);
}

test.describe("策略流水线栏目 E2E", () => {
  test("页面加载: 三卡布局 (当前阶段+左右相邻) + 身份卡", async ({ page }) => {
    await page.goto("/workbench");
    await expect(page.getByText("阶段浏览 · 中间为当前查看阶段").first()).toBeVisible();
    await expect(page.locator(comboBox())).toBeVisible();
    // 任意策略下三卡布局: 当前 + 左右相邻 (至少 3 张阶段卡)
    const h2s = await page.locator("h2").allTextContents();
    const stages = h2s.filter((t) => /^(挖掘|组合|研究|模拟|执行|复盘)/.test(t));
    expect(stages.length).toBeGreaterThanOrEqual(3);
    // 身份卡
    await expect(page.getByText("横截面相对价值").first()).toBeVisible();
  });

  test("策略下拉切换联动: 身份卡跟随", async ({ page }) => {
    await page.goto("/workbench");
    const labels = await waitForOptions(page);
    expect(labels.length).toBeGreaterThanOrEqual(20); // 30 条基线, 容差
    // 切到第二条, 验证身份卡显示其名称 (定位身份卡容器, 避开 select option)
    const name = labels[1].split(" — ")[0];
    await page.locator(comboBox()).selectOption({ label: labels[1] });
    await page.waitForTimeout(600);
    const idCard = page.getByText("横截面相对价值").locator("xpath=ancestor::div[contains(@class,'rounded-xl')][1]");
    await expect(idCard.getByText(name, { exact: false }).first()).toBeVisible();
  });

  test("阶段门控: 组合卡显示候选组合, 执行卡显示执行详情 (浏览跟随)", async ({ page }) => {
    await page.goto("/workbench");
    const labels = await waitForOptions(page);
    await selectBaseStrategy(page, labels);
    // 组合卡是研究的左邻 (paper 当前=模拟, 三卡=研究/模拟/执行) → 先研究再组合
    await gotoStage(page, "研究");
    await gotoStage(page, "组合");
    await expect(page.getByText("候选组合", { exact: false }).first()).toBeVisible({ timeout: 10_000 });
    // 浏览到执行卡 → 执行详情面板出现 (浏览门控: viewStage=live 显示)
    await navigateTo(page, "执行");
    await expect(page.getByText("执行状态", { exact: false }).first()).toBeVisible({ timeout: 10_000 });
    // 离开执行卡 (回当前) → 执行详情隐藏
    await navigateTo(page, "研究");
    await expect(page.getByText("执行状态", { exact: false })).toHaveCount(0);
  });

  test("复盘门控: 复盘卡显示复盘建议区", async ({ page }) => {
    await page.goto("/workbench");
    const labels = await waitForOptions(page);
    await selectBaseStrategy(page, labels);
    await navigateTo(page, "复盘");
    await expect(page.getByText("复盘建议", { exact: false }).first()).toBeVisible({ timeout: 10_000 });
  });

  test("数据一致性: 研究卡指标无重复, 僵尸策略显示 -- (30.13% 事故回归)", async ({ page }) => {
    await page.goto("/workbench");
    const labels = await waitForOptions(page);
    expect(labels.length).toBeGreaterThanOrEqual(20);

    const seen = new Set<string>();
    let ghostCount = 0;
    for (const label of labels) {
      await page.locator(comboBox()).selectOption({ label });
      await page.waitForTimeout(400);
      // 浏览到研究卡 (paper 策略默认在模拟; paused 策略默认已在研究, 点击无害)
      await gotoStage(page, "研究");
      // 等研究卡变中间卡 (4 项指标齐全: 累计收益可见)
      const card = stageCard(page, "研究").locator("xpath=ancestor::div[contains(@class,'rounded-xl')][1]");
      await expect(card.getByText("累计收益", { exact: false }).first()).toBeVisible({ timeout: 10_000 });
      const annual = await researchValue(page, "回测年化");
      const sharpe = await researchValue(page, "回测夏普");
      const mdd = await researchValue(page, "最大回撤");
      const cum = await researchValue(page, "累计收益");

      if (annual === "--" && cum === "--") {
        ghostCount++; // 僵尸/不可用策略: 全 --, 合法
        continue;
      }
      // 有效策略: 4 项齐全, 且四元组全局唯一
      expect(annual).not.toBe("--");
      expect(sharpe).not.toBe("--");
      expect(cum).not.toBe("--");
      const quad = `${annual}|${sharpe}|${mdd}|${cum}`;
      expect(seen.has(quad), `指标重复 (事故回归): ${label} → ${quad}`).toBe(false);
      seen.add(quad);
    }
    // 至少 10 条僵尸 (基线 10, 容差 >=8), 至少 10 条有效
    expect(ghostCount).toBeGreaterThanOrEqual(8);
    expect(seen.size).toBeGreaterThanOrEqual(10);
  });

  test("生命周期记录: 默认折叠, 点击展开有履历", async ({ page }) => {
    await page.goto("/workbench");
    const btn = page.getByText("流水线生命周期记录", { exact: false }).first();
    await expect(btn).toBeVisible();
    // 默认折叠: 内容不可见
    await expect(page.getByText(/seeded|重建: 从注册表恢复/)).toHaveCount(0);
    await btn.click();
    await page.waitForTimeout(500);
    // 展开后: 有生命周期条目 (seeded/重建/pause 等 action)
    await expect(page.getByText(/seeded|重建: 从注册表恢复|晋升变体自动播种|pause/).first()).toBeVisible({ timeout: 10_000 });
  });
});
