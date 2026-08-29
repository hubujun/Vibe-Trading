import { useEffect, useState } from "react";
import { RefreshCw, ShieldCheck, TrendingDown, TrendingUp } from "lucide-react";
import { api, FactorHealthResponse } from "../lib/api";
import { cn } from "../lib/utils";

/** 术语提示: ? 圆圈 + hover 白话解释 (与工作台 Term 同款, 独立页面内嵌) */
const TERMS: Record<string, string> = {
  ic: "IC 信息系数：因子预测方向与真实涨跌的相关系数，范围 -1~1。>0.05 算有预测力，接近 0 就是没用的因子",
  icir: "IC_IR = IC 均值 ÷ IC 波动：衡量因子预测力稳不稳。机构用它排因子优先级，比单看 IC 更可靠",
};
function Term({ k }: { k: keyof typeof TERMS | string }) {
  return (
    <span title={TERMS[k as string] ?? k} className="ml-0.5 inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full bg-muted text-[9px] text-muted-foreground align-middle">
      ?
    </span>
  );
}

export function FactorHealth() {
  const [data, setData] = useState<FactorHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (force = false) => {
    setError(null);
    if (force) setRefreshing(true);
    try {
      const d = await api.getFactorHealth(force);
      setData(d);
    } catch (e) {
      setError(`加载失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const rows = data?.results ?? [];
  const maxPaper = Math.max(1, ...rows.map((r) => r.paper_best_nav ?? 1));

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <ShieldCheck className="h-6 w-6 text-violet-500" /> 因子体检
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            全因子横截面信息含量评估 — IC / IC_IR / 分层收益 + 模拟盘净值对照
            {data && ` · ${data.universe_size} 币 × ${data.days} 天`}
          </p>
        </div>
        <button
          onClick={() => load(true)}
          disabled={refreshing}
          className="flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-muted/60 disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
          重算（拉取行情，约 2-3 分钟）
        </button>
      </div>

      <div className="rounded-xl border bg-card p-4 text-xs text-muted-foreground space-y-1.5">
        <p>
          <span className="font-medium text-foreground">怎么看：</span>
          <Term k="ic" /> = 因子对次日收益的横截面排序能力（越高越好，正=有效）；<Term k="icir" /> =
          稳定性（IC 均值 ÷ IC 波动，机构用它排因子优先级）；IC+ 率 = 有效天数占比；多空年化 =
          单因子 top3 多 bottom3 空的年化收益（含成本，衡量独立赚钱能力）。模拟盘 = 该因子在
          已播种策略里的最佳净值（信息含量高的因子模拟盘应该靠前）。
        </p>
        <p className="text-violet-500">
          基策略 BAB + high52w 正是 IC_IR 第 1 + 第 3 的组合 —— 数据支持，不是拍脑袋。
        </p>
      </div>

      {error && <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-500">{error}</div>}

      {loading ? (
        <div className="flex h-[40vh] items-center justify-center text-muted-foreground">
          <RefreshCw className="h-5 w-5 animate-spin mr-2" /> 首次评估中（拉取 17 币 800 天行情，约 2-3 分钟）…
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border bg-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                <th className="px-4 py-2.5 font-medium">因子</th>
                <th className="px-3 py-2.5 font-medium text-right">IC <Term k="ic" /></th>
                <th className="px-3 py-2.5 font-medium text-right">IC_IR <Term k="icir" /></th>
                <th className="px-3 py-2.5 font-medium text-right">IC+ 率</th>
                <th className="px-3 py-2.5 font-medium text-right">多空年化</th>
                <th className="px-3 py-2.5 font-medium text-right">多空夏普</th>
                <th className="px-3 py-2.5 font-medium text-right">模拟盘最佳净值</th>
                <th className="px-3 py-2.5 font-medium text-right">体检</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const isBase = r.factor === "BAB" || r.factor === "high52w";
                const health =
                  r.ic_ir >= 0.12 ? "优秀" : r.ic_ir >= 0.06 ? "良好" : r.ic_ir >= 0 ? "一般" : "失效";
                const healthColor =
                  r.ic_ir >= 0.12 ? "text-emerald-500" : r.ic_ir >= 0.06 ? "text-lime-500" : r.ic_ir >= 0 ? "text-amber-500" : "text-rose-500";
                const paper = r.paper_best_nav ?? 1;
                return (
                  <tr key={r.factor} className="border-b border-border/40 last:border-0 hover:bg-muted/30">
                    <td className="px-4 py-2.5">
                      <span className={cn("font-mono text-[13px]", isBase && "font-semibold text-violet-500")}>
                        {r.factor}
                      </span>
                      {isBase && <span className="ml-1.5 rounded bg-violet-500/10 px-1.5 py-0.5 text-[10px] text-violet-500">基策略</span>}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-[13px]">{r.ic.toFixed(4)}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-[13px]">{r.ic_ir.toFixed(3)}</td>
                    <td className="px-3 py-2.5 text-right font-mono text-[13px]">{(r.ic_pos * 100).toFixed(0)}%</td>
                    <td className="px-3 py-2.5 text-right font-mono text-[13px]">
                      {r.ls_annual == null ? <span className="text-muted-foreground">—</span> : (
                        <span className={cn(r.ls_annual >= 0 ? "text-emerald-500" : "text-rose-500")}>
                          {r.ls_annual >= 0 ? "+" : ""}{r.ls_annual}%
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-[13px]">{r.ls_sharpe.toFixed(2)}</td>
                    <td className="px-3 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <span className="font-mono text-[13px]">{paper.toFixed(4)}</span>
                        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                          <div
                            className={cn("h-full", paper >= 1 ? "bg-emerald-400" : "bg-rose-400")}
                            style={{ width: `${Math.max(4, (paper / maxPaper) * 100)}%` }}
                          />
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium", healthColor)}>
                        {r.ic_ir >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                        {health}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">
        数据源: OKX 永续 800 天日线（有多少测多少，新币按上市日参与）· 多空分层含 0.2% 双边成本 ·
        每 6 小时自动缓存，重算按钮强制刷新 · 评估逻辑见 src/strategy/factor_health.py
      </p>
    </div>
  );
}
