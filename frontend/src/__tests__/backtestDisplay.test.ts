import { describe, expect, it } from "vitest";

import { backtestValue, type BacktestDisplay } from "../lib/backtestDisplay";

const NORMAL: BacktestDisplay = { annual: 48.26, sharpe: 1.48, max_dd: -20.39, cum: 137.06 };
const GHOST: BacktestDisplay = {
  annual: null, sharpe: null, max_dd: null, cum: null,
  error: "因子不可用",
};
const EMPTY: BacktestDisplay = {};

describe("backtestValue — 研究卡回测指标取值 (2026-08-30 事故回归)", () => {
  it("正常策略显示自身指标", () => {
    expect(backtestValue(NORMAL, "cum")).toBe(137.06);
    expect(backtestValue(NORMAL, "annual")).toBe(48.26);
  });

  it("error 标记策略 → undefined (显示 --), 绝不 fallback — 事故防线", () => {
    // 之前: cum=null ?? combo2.cum → 10 条策略全显示 30.13%
    expect(backtestValue(GHOST, "cum", 30.13)).toBeUndefined();
    expect(backtestValue(GHOST, "annual", 37.93)).toBeUndefined();
    expect(backtestValue(GHOST, "sharpe", 1.2)).toBeUndefined();
  });

  it("空值 fallback (仅无 error 时)", () => {
    expect(backtestValue(EMPTY, "cum", 30.13)).toBe(30.13);
    expect(backtestValue(EMPTY, "cum")).toBeUndefined();
  });

  it("有值不 fallback", () => {
    expect(backtestValue(NORMAL, "annual", 37.93)).toBe(48.26);
  });

  it("null 值视为空 (可 fallback)", () => {
    // 基策略 _BASE_ 历史数据 cum=null 的场景
    const bt: BacktestDisplay = { annual: 37.44, sharpe: 1.19, max_dd: -26.38, cum: null };
    expect(backtestValue(bt, "cum", 30.13)).toBe(30.13);
    expect(backtestValue(bt, "annual")).toBe(37.44);
  });

  it("undefined/无 bt → undefined", () => {
    expect(backtestValue(undefined, "cum")).toBeUndefined();
    expect(backtestValue(null, "cum", 1.0)).toBeUndefined();
  });
});
