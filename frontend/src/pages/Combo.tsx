import { useEffect, useMemo, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Gauge, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { api, type ComboSummary } from "@/lib/api";
import { echarts } from "@/lib/echarts";
import { useThemeDark } from "@/lib/theme-store";
import { cn } from "@/lib/utils";

export function Combo() {
  const [data, setData] = useState<ComboSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const dark = useThemeDark();

  const load = async (signal?: AbortSignal) => {
    try {
      setLoading(true);
      const d = await api.getComboSummary(signal);
      setData(d);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        toast.error("加载策略组合数据失败");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    const timer = setInterval(() => load(ctrl.signal), 60_000);
    return () => {
      ctrl.abort();
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const combo2 = data?.metrics.backtest["COMBO2(BAB+52w)"];
  const nav = data?.paper.nav;
  const startedAt = data?.paper.started_at;
  const signalDate = data?.signal.date;

  // 模拟盘净值曲线（基于 trades 累计）
  const navChart = useMemo(() => {
    if (!data?.paper.trades?.length) return null;
    let nav = data.paper.nav ?? 1;
    const rets = [...data.paper.trades];
    // 从后往前反推每段 NAV
    const reversed: { d: string; v: number }[] = [];
    for (let i = rets.length - 1; i >= 0; i--) {
      reversed.unshift({ d: rets[i].to, v: nav });
      nav = nav / (1 + rets[i].ret / 100);
    }
    reversed.unshift({ d: rets[0].from, v: nav });
    // 按日期聚合去重（相同日期保留最新一个点），避免脏数据造成横坐标重复
    const byDate = new Map<string, { d: string; v: number }>();
    for (const p of reversed) byDate.set(p.d, p);
    return Array.from(byDate.values());
  }, [data]);

  useEffect(() => {
    if (!navChart || !data) return;
    const el = document.getElementById("combo-nav-chart");
    if (!el) return;
    const chart = echarts.init(el, dark ? "dark" : undefined);
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", valueFormatter: (v: number) => v.toFixed(4) },
      grid: { left: 50, right: 20, top: 20, bottom: 30 },
      xAxis: { type: "category", data: navChart.map(p => p.d), axisLabel: { color: "#8b93a7" } },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { color: "#8b93a7", formatter: (v: number) => v.toFixed(3) },
        splitLine: { lineStyle: { color: "#1a2340" } },
      },
      series: [
        {
          type: "line",
          data: navChart.map(p => p.v),
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { color: "#00d4ff", width: 2 },
          itemStyle: { color: "#00d4ff" },
          areaStyle: {
            color: {
              type: "linear", x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(0,212,255,.25)" },
                { offset: 1, color: "rgba(0,212,255,0)" },
              ],
            },
          },
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [navChart, data, dark]);

  const kpis = [
    {
      label: "模拟盘净值",
      value: nav != null ? nav.toFixed(4) : "--",
      hint: startedAt ? `起于 ${startedAt}` : "等待首个信号",
      color: "text-emerald-400",
    },
    {
      label: "回测年化 (COMBO2)",
      value: combo2 ? `${combo2.annual > 0 ? "+" : ""}${combo2.annual}%` : "--",
      hint: combo2 ? `回测期 ${data?.metrics.period ?? ""}` : "回测数据生成中",
      color: "text-cyan-400",
    },
    {
      label: "回测夏普 (COMBO2)",
      value: combo2 ? combo2.sharpe.toFixed(2) : "--",
      hint: combo2 ? `最大回撤 ${combo2.max_dd}%` : "",
      color: "text-purple-400",
    },
    {
      label: "信号日期",
      value: signalDate ?? "--",
      hint: "每日 07:00 自动更新",
      color: "text-amber-400",
    },
  ];

  const btRows = data?.metrics.backtest ? Object.entries(data.metrics.backtest) : [];
  const icRows = data?.metrics.ic ? Object.entries(data.metrics.ic) : [];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Gauge className="h-6 w-6 text-cyan-400" />
            策略组合 · BAB + high52w
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            低波动异象 × 52周高点动量 · 等权横截面 · 多 top3 空 bottom3 · 单边成本 0.1%
          </p>
        </div>
        <button
          onClick={() => load()}
          className="inline-flex items-center gap-1.5 rounded-md border border-input bg-background px-3 py-1.5 text-sm font-medium hover:bg-accent"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          刷新
        </button>
      </div>

      {loading && !data ? (
        <div className="flex h-[40vh] items-center justify-center text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载中…
        </div>
      ) : (
        <>
          {/* KPI */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {kpis.map(k => (
              <div key={k.label} className="rounded-xl border bg-card p-4">
                <div className="text-xs text-muted-foreground">{k.label}</div>
                <div className={cn("text-2xl font-bold mt-1 font-mono", k.color)}>{k.value}</div>
                <div className="text-xs text-muted-foreground mt-1">{k.hint}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* 今日信号 */}
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
                今日信号
                {signalDate && (
                  <span className="text-xs font-normal text-muted-foreground">({signalDate})</span>
                )}
              </h2>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-xs text-emerald-400 mb-2 flex items-center gap-1">
                    <ArrowUpRight className="h-3.5 w-3.5" /> 做多
                  </div>
                  {data?.signal.longs.map(s => (
                    <div
                      key={s.symbol}
                      className="mb-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2.5"
                    >
                      <div className="flex justify-between items-center">
                        <span className="font-mono text-sm font-semibold">{s.symbol}</span>
                        <span className="text-xs text-emerald-400 font-mono">{s.score.toFixed(2)}</span>
                      </div>
                      <div className="mt-1.5 h-1.5 rounded-full bg-muted overflow-hidden">
                        <div
                          className="h-full bg-emerald-400"
                          style={{ width: `${Math.min(100, Math.max(8, (s.score + 2) * 30))}%` }}
                        />
                      </div>
                    </div>
                  ))}
                  {!data?.signal.longs.length && (
                    <div className="text-xs text-muted-foreground">等待每日信号…</div>
                  )}
                </div>
                <div>
                  <div className="text-xs text-rose-400 mb-2 flex items-center gap-1">
                    <ArrowDownRight className="h-3.5 w-3.5" /> 做空
                  </div>
                  {data?.signal.shorts.map(s => (
                    <div
                      key={s.symbol}
                      className="mb-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-2.5"
                    >
                      <div className="flex justify-between items-center">
                        <span className="font-mono text-sm font-semibold">{s.symbol}</span>
                        <span className="text-xs text-rose-400 font-mono">{s.score.toFixed(2)}</span>
                      </div>
                      <div className="mt-1.5 h-1.5 rounded-full bg-muted overflow-hidden">
                        <div
                          className="h-full bg-rose-400"
                          style={{ width: `${Math.min(100, Math.max(8, (Math.abs(s.score) + 0.5) * 30))}%` }}
                        />
                      </div>
                    </div>
                  ))}
                  {!data?.signal.shorts.length && (
                    <div className="text-xs text-muted-foreground">等待每日信号…</div>
                  )}
                </div>
              </div>
            </div>

            {/* 模拟盘净值 */}
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3">模拟盘净值追踪</h2>
              {navChart ? (
                <div id="combo-nav-chart" className="h-[220px] w-full" />
              ) : (
                <div className="flex h-[220px] items-center justify-center text-xs text-muted-foreground">
                  净值曲线将在积累 2+ 次调仓后显示
                </div>
              )}
              <div className="mt-2 text-xs text-muted-foreground">
                {data?.paper.trades?.length ?? 0} 次调仓记录 · 每日 07:00 cron 自动更新
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* 回测对比 */}
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3">回测对比（800天 · 含成本）</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-muted-foreground border-b">
                      <th className="text-left py-2 pr-3">策略</th>
                      <th className="text-right py-2 px-2">年化</th>
                      <th className="text-right py-2 px-2">夏普</th>
                      <th className="text-right py-2 px-2">最大回撤</th>
                      <th className="text-right py-2 px-2">累计</th>
                    </tr>
                  </thead>
                  <tbody>
                    {btRows.map(([name, m]) => (
                      <tr
                        key={name}
                        className={cn(
                          "border-b border-muted/50",
                          name.includes("COMBO2") && "bg-cyan-500/5",
                        )}
                      >
                        <td className="py-2 pr-3 font-medium">{name}</td>
                        <td className={cn("text-right py-2 px-2 font-mono", m.annual >= 0 ? "text-emerald-400" : "text-rose-400")}>
                          {m.annual > 0 ? "+" : ""}{m.annual}%
                        </td>
                        <td className="text-right py-2 px-2 font-mono">{m.sharpe.toFixed(2)}</td>
                        <td className="text-right py-2 px-2 font-mono text-rose-400">{m.max_dd}%</td>
                        <td className={cn("text-right py-2 px-2 font-mono", m.cum >= 0 ? "text-emerald-400" : "text-rose-400")}>
                          {m.cum > 0 ? "+" : ""}{m.cum}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* 因子 IC */}
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3">因子 IC（2024-06 ~ 2026-08）</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-muted-foreground border-b">
                      <th className="text-left py-2 pr-3">因子</th>
                      <th className="text-right py-2 px-2">IC均值</th>
                      <th className="text-right py-2 px-2">IR</th>
                      <th className="text-right py-2 px-2">IC+ 比率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {icRows.map(([name, m]) => (
                      <tr key={name} className="border-b border-muted/50">
                        <td className="py-2 pr-3 font-medium">{name}</td>
                        <td className={cn("text-right py-2 px-2 font-mono", m.ic_mean >= 0 ? "text-emerald-400" : "text-rose-400")}>
                          {m.ic_mean > 0 ? "+" : ""}{m.ic_mean.toFixed(4)}
                        </td>
                        <td className={cn("text-right py-2 px-2 font-mono", m.ir >= 0 ? "text-emerald-400" : "text-rose-400")}>
                          {m.ir > 0 ? "+" : ""}{m.ir.toFixed(3)}
                        </td>
                        <td className="text-right py-2 px-2 font-mono">{m.ic_pos.toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* 假设 */}
          <div className="rounded-xl border bg-card p-4">
            <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-emerald-400" />
              Hypothesis Registry
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {data?.hypotheses.map(h => (
                <div key={h.hypothesis_id} className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{h.title}</span>
                    <span className="shrink-0 rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-400">
                      {h.status}
                    </span>
                  </div>
                  <div className="mt-1.5 font-mono text-[10px] text-muted-foreground">{h.hypothesis_id}</div>
                </div>
              ))}
              {!data?.hypotheses.length && (
                <div className="text-xs text-muted-foreground">暂无假设记录</div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
