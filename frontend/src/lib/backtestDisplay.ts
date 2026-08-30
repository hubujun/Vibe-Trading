/**
 * 回测指标显示取值逻辑 (2026-08-30 事故回归: 多条策略显示相同累计收益).
 *
 * 事故: strategy_backtest.cum=null (僵尸策略 error 标记) 时, 前端 ?? 操作符
 * fallback 到共享的 combo2 (旧基准累计 30.13%), 导致 10 条策略显示相同值.
 *
 * 规则: error 标记 (僵尸/不可用策略) → undefined (显示 "--");
 *       有值 → 策略自身值; 无值 → fallback (仅基策略场景).
 */
export interface BacktestDisplay {
  annual?: number | null;
  sharpe?: number | null;
  max_dd?: number | null;
  cum?: number | null;
  error?: string;
}

export type BacktestKey = "annual" | "sharpe" | "max_dd" | "cum";

/**
 * 研究卡回测指标取值:
 * - bt.error 存在 → undefined (显示 "--", 绝不 fallback — 事故防线)
 * - bt[key] 有值 → 该值
 * - 否则 → fallback ?? undefined
 */
export function backtestValue(
  bt: BacktestDisplay | undefined | null,
  key: BacktestKey,
  fallback?: number | null,
): number | null | undefined {
  if (!bt || bt.error) return undefined;
  const v = bt[key];
  if (v != null) return v;
  return fallback ?? undefined;
}
