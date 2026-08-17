import { useEffect, useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  ChevronRight,
  Cpu,
  FlaskConical,
  Gauge,
  History,
  Layers,
  Loader2,
  Pause,
  Play,
  Receipt,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type WorkbenchResponse,
  type WorkbenchStrategy,
} from "@/lib/api";
import { echarts } from "@/lib/echarts";
import { useThemeDark } from "@/lib/theme-store";
import { cn } from "@/lib/utils";

/**
 * 策略流水线工作台 (方案C)
 *
 * 一屏看全策略生命周期: 研究(回测/IC) → 模拟(纸面) → 执行(Autopilot) → 复盘(假设).
 * 后端 /api/workbench 聚合 combo 研究数据 + autopilot 执行数据,
 * 顶部操作按钮推进/回退/暂停/恢复策略级状态机.
 */

const PHASE_META: Record<
  string,
  { label: string; color: string; border: string; bg: string; icon: typeof Gauge }
> = {
  mine: {
    label: "挖掘",
    color: "text-amber-400",
    border: "border-amber-500/40",
    bg: "bg-amber-500/10",
    icon: FlaskConical,
  },
  compose: {
    label: "组合",
    color: "text-purple-400",
    border: "border-purple-500/40",
    bg: "bg-purple-500/10",
    icon: Layers,
  },
  research: {
    label: "研究",
    color: "text-cyan-400",
    border: "border-cyan-500/40",
    bg: "bg-cyan-500/10",
    icon: FlaskConical,
  },
  paper: {
    label: "模拟",
    color: "text-cyan-400",
    border: "border-cyan-500/40",
    bg: "bg-cyan-500/10",
    icon: TrendingUp,
  },
  live: {
    label: "执行",
    color: "text-emerald-400",
    border: "border-emerald-500/40",
    bg: "bg-emerald-500/10",
    icon: Cpu,
  },
  review: {
    label: "复盘",
    color: "text-purple-400",
    border: "border-purple-500/40",
    bg: "bg-purple-500/10",
    icon: Sparkles,
  },
  paused: {
    label: "暂停",
    color: "text-orange-400",
    border: "border-orange-500/40",
    bg: "bg-orange-500/10",
    icon: Pause,
  },
};

// 完整生命周期流水线: 挖掘 → 组合 → 研究 → 模拟 → 执行 → 复盘
const PHASE_ORDER = ["mine", "compose", "research", "paper", "live", "review"];

function fmtPct(v: number | undefined | null, signed = true): string {
  if (v == null || Number.isNaN(v)) return "--";
  return `${signed && v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function Workbench() {
  const [data, setData] = useState<WorkbenchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const dark = useThemeDark();

  const load = async (signal?: AbortSignal) => {
    try {
      setLoading(true);
      setData(await api.getWorkbench(signal));
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        toast.error("加载工作台数据失败");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    const timer = setInterval(() => load(ctrl.signal), 30_000);
    return () => {
      ctrl.abort();
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const strategy: WorkbenchStrategy | undefined = data?.strategies[0];
  const phase = strategy?.phase ?? "research";
  const combo2 = data?.combo.metrics.backtest["COMBO2(BAB+52w)"];
  const autopilot = data?.autopilot;
  const hypotheses = data?.combo.hypotheses ?? [];
  const review = data?.review;

  // --- 生命周期迁移 ---
  const transition = async (action: string, label: string) => {
    if (!strategy || mutating) return;
    setMutating(true);
    try {
      const updated = await api.transitionStrategy(strategy.strategy_id, action);
      setData((prev) =>
        prev ? { ...prev, strategies: [updated, ...prev.strategies.filter(s => s.strategy_id !== updated.strategy_id)] } : prev,
      );
      toast.success(`${label}成功 · 当前阶段: ${PHASE_META[updated.phase]?.label ?? updated.phase}`);
    } catch (e) {
      const detail = (e as { detail?: string })?.detail;
      toast.error(detail ?? `${label}失败`);
    } finally {
      setMutating(false);
    }
  };

  // --- 模拟盘净值曲线 (反推法, 同 Combo 页) ---
  const navChart = useMemo(() => {
    const trades = data?.combo.paper.trades;
    if (!trades?.length) return null;
    let nav = data?.combo.paper.nav ?? 1;
    const reversed: { d: string; v: number }[] = [];
    for (let i = trades.length - 1; i >= 0; i--) {
      reversed.unshift({ d: trades[i].to, v: nav });
      nav = nav / (1 + trades[i].ret / 100);
    }
    reversed.unshift({ d: trades[0].from, v: nav });
    const byDate = new Map<string, { d: string; v: number }>();
    for (const p of reversed) byDate.set(p.d, p);
    return Array.from(byDate.values());
  }, [data]);

  useEffect(() => {
    if (!navChart) return;
    const el = document.getElementById("workbench-nav-chart");
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
  }, [navChart, dark]);

  // --- 顶部操作按钮 (按阶段推导) ---
  const actions: { action: string; label: string; kind: "forward" | "pause" | "ghost" }[] = [];
  if (phase === "research") actions.push({ action: "start_paper", label: "启动模拟", kind: "forward" });
  if (phase === "paper") {
    actions.push({ action: "promote_live", label: "上线执行", kind: "forward" });
    actions.push({ action: "pause", label: "暂停", kind: "pause" });
  }
  if (phase === "live") actions.push({ action: "pause", label: "暂停", kind: "pause" });
  if (phase === "paused") actions.push({ action: "resume", label: "恢复", kind: "forward" });
  actions.push({ action: "back_to_research", label: "回到研究", kind: "ghost" });

  const isPaused = phase === "paused";
  const effectivePhase = isPaused ? (strategy?.paused_from ?? "paper") : phase;

  // 变体候选数 (exploring/testing 假设)
  const variantCount = hypotheses.filter(h => h.status === "exploring" || h.status === "testing").length;
  const backtestedCount = Object.keys(review?.variant_metrics ?? {}).length;
  const testingCount = hypotheses.filter(h => h.status === "testing").length;

  // 流水线节点状态: 挖掘/组合为数据驱动, 研究~复盘由策略阶段决定
  const nodeStatus = (p: string, i: number): "passed" | "current" | "pending" => {
    if (p === "mine") return (data?.autopilot_factors?.zoo_count ?? 0) > 0 ? "passed" : "pending";
    if (p === "compose") return variantCount > 0 ? "passed" : "pending";
    const strategyIdx = ["research", "paper", "live", "review"].indexOf(effectivePhase);
    const nodeIdx = i - 2; // 研究=0, 模拟=1, 执行=2, 复盘=3
    if (nodeIdx === strategyIdx) return "current";
    if (nodeIdx < strategyIdx) return "passed";
    return "pending";
  };

  const stageStats: Record<string, { label: string; value: string; color?: string }[]> = {
    mine: [
      { label: "zoo 因子", value: `${data?.autopilot_factors?.zoo_count ?? "--"}` },
      { label: "活跃（交易中）", value: `${data?.autopilot_factors?.active?.length ?? "--"}`, color: "text-emerald-400" },
      { label: "待评估", value: `${data?.autopilot_factors?.pending?.length ?? "--"}`, color: "text-amber-400" },
      { label: "退役（被三关拒绝）", value: `${data?.autopilot_factors?.retired?.length ?? "--"}`, color: "text-muted-foreground" },
    ],
    compose: [
      { label: "变体候选", value: `${variantCount}`, color: "text-purple-400" },
      { label: "已自动回测", value: `${backtestedCount}`, color: "text-cyan-400" },
      { label: "晋升 testing", value: `${testingCount}`, color: "text-amber-400" },
      { label: "自动回测", value: "每日 08:45" },
    ],
    research: [
      { label: "回测年化", value: combo2 ? fmtPct(combo2.annual) : "--", color: "text-emerald-400" },
      { label: "回测夏普", value: combo2 ? combo2.sharpe.toFixed(2) : "--" },
      { label: "最大回撤", value: combo2 ? fmtPct(combo2.max_dd) : "--", color: "text-rose-400" },
      { label: "累计收益", value: combo2 ? fmtPct(combo2.cum) : "--", color: "text-emerald-400" },
    ],
    paper: [
      { label: "模拟盘净值", value: data?.combo.paper.nav != null ? data.combo.paper.nav.toFixed(4) : "--", color: "text-emerald-400" },
      { label: "调仓次数", value: `${data?.combo.paper.trades?.length ?? 0}` },
      { label: "起于", value: data?.combo.paper.started_at ? String(data.combo.paper.started_at).slice(0, 10) : "--" },
      { label: "最新信号", value: data?.combo.signal.date ?? "--", color: "text-amber-400" },
    ],
    live: [
      { label: "引擎存活", value: autopilot?.health.alive ? "是" : "否", color: autopilot?.health.alive ? "text-emerald-400" : "text-rose-400" },
      { label: "熔断", value: autopilot?.halt.halted ? "已触发" : "无", color: autopilot?.halt.halted ? "text-rose-400" : "text-emerald-400" },
      { label: "今日订单", value: autopilot ? `${autopilot.counter.count} / ${autopilot.config.max_trades_per_day}` : "--" },
      { label: "流水线", value: autopilot ? (PHASE_META[autopilot.pipeline.phase]?.label ?? autopilot.pipeline.phase) : "--" },
    ],
    review: [
      { label: "vs 回测", value: review?.vs_backtest.outperforming == null ? "样本不足" : review.vs_backtest.outperforming ? "跑赢" : "跑输", color: review?.vs_backtest.outperforming ? "text-emerald-400" : review?.vs_backtest.outperforming == null ? undefined : "text-rose-400" },
      { label: "回撤超限", value: review?.vs_backtest.dd_breach ? "是" : "否", color: review?.vs_backtest.dd_breach ? "text-rose-400" : "text-emerald-400" },
      { label: "信号新鲜度", value: review?.signal_health.stale ? "过期" : "正常", color: review?.signal_health.stale ? "text-amber-400" : "text-emerald-400" },
      { label: "数据新鲜度", value: review?.data_freshness.stale ? "过期" : "正常", color: review?.data_freshness.stale ? "text-amber-400" : "text-emerald-400" },
    ],
  };

  const history = [...(strategy?.phase_history ?? [])].reverse().slice(0, 8);
  const icRows = data?.combo.metrics.ic ? Object.entries(data.combo.metrics.ic) : [];
  const btRows = data?.combo.metrics.backtest ? Object.entries(data.combo.metrics.backtest) : [];

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Gauge className="h-6 w-6 text-cyan-400" />
            策略流水线工作台
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            挖掘 → 组合 → 研究 → 模拟 → 执行 → 复盘 · 完整闭环 · 每 30s 自动刷新
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => load()}
            className="inline-flex items-center gap-1.5 rounded-md border border-input bg-background px-3 py-1.5 text-sm font-medium hover:bg-accent"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            刷新
          </button>
          {actions.map(a => (
            <button
              key={a.action}
              disabled={mutating}
              onClick={() => transition(a.action, a.label)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition disabled:opacity-50",
                a.kind === "forward" && "border-cyan-500/50 bg-cyan-500/10 text-cyan-300 hover:bg-cyan-500/20",
                a.kind === "pause" && "border-amber-500/50 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20",
                a.kind === "ghost" && "border-input bg-background hover:bg-accent",
              )}
            >
              {a.kind === "forward" && <Play className="h-3.5 w-3.5" />}
              {a.kind === "pause" && <Pause className="h-3.5 w-3.5" />}
              {a.kind === "ghost" && <Undo2 className="h-3.5 w-3.5" />}
              {mutating ? "处理中…" : a.label}
            </button>
          ))}
        </div>
      </div>

      {/* Strategy identity */}
      {strategy && (
        <div className="rounded-xl border bg-card p-4">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-sm font-semibold">{strategy.name}</span>
            <span className={cn("rounded-full px-2.5 py-0.5 text-xs font-semibold", PHASE_META[effectivePhase].bg, PHASE_META[effectivePhase].color)}>
              {isPaused ? "已暂停" : PHASE_META[effectivePhase].label}
            </span>
            <span className="text-xs text-muted-foreground">
              因子: {strategy.factors.join(" + ")} · 权重: {Object.entries(strategy.weights).map(([k, v]) => `${k} ${v * 100}%`).join(" / ")}
            </span>
            <span className="text-xs text-muted-foreground">{strategy.universe_size} 币 · {strategy.rebalance}</span>
            <span className="text-xs text-muted-foreground">
              杠杆 ×{strategy.params?.exposure_multiplier ?? 1.0}
            </span>
            <span className="text-xs text-muted-foreground ml-auto">{strategy.description}</span>
          </div>
        </div>
      )}

      {loading && !data ? (
        <div className="flex h-[40vh] items-center justify-center text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载中…
        </div>
      ) : (
        <>
          {/* Pipeline nodes */}
          <div className="rounded-xl border bg-card p-5">
            <div className="flex items-center">
              {PHASE_ORDER.map((p, i) => {
                const meta = PHASE_META[p];
                const Icon = meta.icon;
                const st = nodeStatus(p, i);
                const active = st === "current";
                const passed = st === "passed";
                return (
                  <div key={p} className={cn("flex items-center", i > 0 && "flex-1")}>
                    {i > 0 && (
                      <div className={cn("h-0.5 flex-1 mx-2 rounded", passed || active ? "bg-gradient-to-r from-cyan-500/60 to-cyan-400/60" : "bg-muted")} />
                    )}
                    <div className="flex flex-col items-center gap-1.5">
                      <div
                        className={cn(
                          "flex h-11 w-11 items-center justify-center rounded-full border-2 transition-all",
                          active ? cn(meta.border, meta.bg, "scale-110") : passed ? "border-cyan-500/50 bg-cyan-500/10" : "border-border bg-muted/40",
                        )}
                      >
                        <Icon className={cn("h-5 w-5", active ? meta.color : passed ? "text-cyan-400" : "text-muted-foreground")} />
                      </div>
                      <span className={cn("text-xs font-medium", active ? meta.color : "text-muted-foreground")}>
                        {meta.label}
                        {i === 5 && <ChevronRight className="inline h-3 w-3 ml-0.5" />}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Stage cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {PHASE_ORDER.map((p, i) => {
              const meta = PHASE_META[p];
              const stats = stageStats[p] ?? [];
              const st = nodeStatus(p, i);
              const active = st === "current";
              const passed = st === "passed";
              return (
                <div key={p} className={cn("rounded-xl border bg-card p-4 transition-all", active ? cn(meta.border, "ring-1 ring-current/10") : passed ? "border-cyan-500/30" : "border-border")}>
                  <div className="flex items-center gap-2 mb-3">
                    <meta.icon className={cn("h-4 w-4", active ? meta.color : passed ? "text-cyan-400" : "text-muted-foreground")} />
                    <h2 className="text-sm font-semibold">{meta.label}</h2>
                    {active && <span className={cn("ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold", meta.bg, meta.color)}>当前</span>}
                    {passed && p !== "review" && <span className="ml-auto rounded-full bg-cyan-500/10 px-2 py-0.5 text-[10px] font-semibold text-cyan-400">✓</span>}
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    {stats.map(s => (
                      <div key={s.label}>
                        <div className="text-[11px] text-muted-foreground">{s.label}</div>
                        <div className={cn("font-mono text-lg font-bold mt-0.5", s.color ?? "text-foreground")}>{s.value}</div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Today signal + NAV */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
                今日信号
                {data?.combo.signal.date && (
                  <span className="text-xs font-normal text-muted-foreground">({data.combo.signal.date})</span>
                )}
              </h2>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-xs text-emerald-400 mb-2 flex items-center gap-1">
                    <ArrowUpRight className="h-3.5 w-3.5" /> 做多
                  </div>
                  {data?.combo.signal.longs.map(s => (
                    <div key={s.symbol} className="mb-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2.5">
                      <div className="flex justify-between items-center">
                        <span className="font-mono text-sm font-semibold">{s.symbol}</span>
                        <span className="text-xs text-emerald-400 font-mono">{s.score.toFixed(2)}</span>
                      </div>
                      <div className="mt-1.5 h-1.5 rounded-full bg-muted overflow-hidden">
                        <div className="h-full bg-emerald-400" style={{ width: `${Math.min(100, Math.max(8, (s.score + 2) * 30))}%` }} />
                      </div>
                    </div>
                  ))}
                  {!data?.combo.signal.longs.length && <div className="text-xs text-muted-foreground">等待每日信号…</div>}
                </div>
                <div>
                  <div className="text-xs text-rose-400 mb-2 flex items-center gap-1">
                    <ArrowDownRight className="h-3.5 w-3.5" /> 做空
                  </div>
                  {data?.combo.signal.shorts.map(s => (
                    <div key={s.symbol} className="mb-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-2.5">
                      <div className="flex justify-between items-center">
                        <span className="font-mono text-sm font-semibold">{s.symbol}</span>
                        <span className="text-xs text-rose-400 font-mono">{s.score.toFixed(2)}</span>
                      </div>
                      <div className="mt-1.5 h-1.5 rounded-full bg-muted overflow-hidden">
                        <div className="h-full bg-rose-400" style={{ width: `${Math.min(100, Math.max(8, (Math.abs(s.score) + 0.5) * 30))}%` }} />
                      </div>
                    </div>
                  ))}
                  {!data?.combo.signal.shorts.length && <div className="text-xs text-muted-foreground">等待每日信号…</div>}
                </div>
              </div>
            </div>

            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3">模拟盘净值追踪</h2>
              {navChart ? (
                <div id="workbench-nav-chart" className="h-[220px] w-full" />
              ) : (
                <div className="flex h-[220px] items-center justify-center text-xs text-muted-foreground">
                  净值曲线将在积累 2+ 次调仓后显示
                </div>
              )}
              <div className="mt-2 text-xs text-muted-foreground">
                {data?.combo.paper.trades?.length ?? 0} 次调仓记录 · 每日 07:00 自动更新
              </div>
            </div>
          </div>

          {/* Backtest + IC */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
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
                      <tr key={name} className={cn("border-b border-muted/50", name.includes("COMBO2") && "bg-cyan-500/5")}>
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

            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3">因子 IC</h2>
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

          {/* 因子挖掘 (Alpha Zoo) */}
          <div className="rounded-xl border bg-card p-4">
            <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
              <FlaskConical className="h-4 w-4 text-amber-400" />
              因子挖掘（Alpha Zoo）
              <span className="text-xs font-normal text-muted-foreground">
                Autopilot discovering 阶段 · factor miner + 过拟合三关准入
              </span>
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              {[
                { label: "zoo 因子总数", value: `${data?.autopilot_factors?.zoo_count ?? "--"}` },
                { label: "活跃（交易中）", value: `${data?.autopilot_factors?.active?.length ?? "--"}`, color: "text-emerald-400" },
                { label: "待评估", value: `${data?.autopilot_factors?.pending?.length ?? "--"}`, color: "text-amber-400" },
                { label: "已退役", value: `${data?.autopilot_factors?.retired?.length ?? "--"}`, color: "text-muted-foreground" },
              ].map(k => (
                <div key={k.label} className="rounded-lg border border-border/60 p-3">
                  <div className="text-[11px] text-muted-foreground">{k.label}</div>
                  <div className={cn("font-mono text-lg font-bold mt-0.5", k.color ?? "text-foreground")}>{k.value}</div>
                </div>
              ))}
            </div>
            {data?.autopilot_factors?.active?.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-muted-foreground border-b">
                      <th className="text-left py-2 pr-3">因子</th>
                      <th className="text-left py-2 px-2">生命周期</th>
                      <th className="text-right py-2 px-2">交易数</th>
                      <th className="text-right py-2 px-2">胜率</th>
                      <th className="text-right py-2 px-2">Profit Factor</th>
                      <th className="text-right py-2 px-2">Sharpe</th>
                      <th className="text-right py-2 px-2">IC</th>
                      <th className="text-right py-2 px-2">已实现 PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.autopilot_factors.active.map(f => {
                      const s = data.autopilot_factor_stats?.[f.alpha_id];
                      return (
                        <tr key={f.alpha_id} className="border-b border-muted/50">
                          <td className="py-2 pr-3 font-mono text-xs">{f.alpha_id}</td>
                          <td className="py-2 px-2 text-xs text-muted-foreground">{f.lifecycle || "--"}</td>
                          <td className="py-2 px-2 text-right font-mono text-xs">{s?.trades ?? 0}</td>
                          <td className={cn("py-2 px-2 text-right font-mono text-xs", (s?.win_rate ?? 0) >= 0.5 ? "text-emerald-400" : "text-rose-400")}>
                            {s ? `${(s.win_rate * 100).toFixed(1)}%` : "--"}
                          </td>
                          <td className={cn("py-2 px-2 text-right font-mono text-xs", (s?.profit_factor ?? 0) >= 1 ? "text-emerald-400" : "text-rose-400")}>
                            {s ? (s.profit_factor == null ? "∞" : s.profit_factor.toFixed(2)) : "--"}
                          </td>
                          <td className={cn("py-2 px-2 text-right font-mono text-xs", (s?.sharpe ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400")}>
                            {s ? s.sharpe.toFixed(2) : "--"}
                          </td>
                          <td className={cn("py-2 px-2 text-right font-mono text-xs", (f.ic_mean ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400")}>
                            {f.ic_mean != null ? `${f.ic_mean >= 0 ? "+" : ""}${f.ic_mean.toFixed(3)}` : "--"}
                          </td>
                          <td className={cn("py-2 px-2 text-right font-mono text-xs", (s?.realized_pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400")}>
                            {s ? `${s.realized_pnl >= 0 ? "+" : ""}$${s.realized_pnl.toLocaleString()}` : "--"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">
                {data?.autopilot_factors ? "暂无活跃挖掘因子" : "挖掘数据不可用（Autopilot 未启动）"}
              </div>
            )}
            {data?.autopilot_factors?.retired?.length ? (
              <>
                <div className="text-xs text-muted-foreground mt-4 mb-2">已退役因子</div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-xs text-muted-foreground border-b">
                        <th className="text-left py-2 pr-3">因子</th>
                        <th className="text-left py-2 px-2">退役时间</th>
                        <th className="text-left py-2 px-2">原因</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.autopilot_factors.retired.map(f => (
                        <tr key={f.alpha_id} className="border-b border-muted/50">
                          <td className="py-2 pr-3 font-mono text-xs text-rose-400">{f.alpha_id}</td>
                          <td className="py-2 px-2 font-mono text-xs text-muted-foreground">{f.retired_at ? String(f.retired_at).slice(0, 16).replace("T", " ") : "--"}</td>
                          <td className="py-2 px-2 text-xs text-muted-foreground">{f.reason || "--"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}
            {data?.autopilot_factors && (
              <div className="mt-3 text-xs text-muted-foreground">
                {data.autopilot_factors.pending.length > 0 && `待评估: ${data.autopilot_factors.pending.join(", ")} · `}
                zoo 共 {data.autopilot_factors.zoo_count} 个因子 · 因子级变体生成器会自动从 zoo 挑选新挖掘因子加入组合候选（研究 → 模拟 → 执行 → 复盘 的螺旋中新因子从这里进入）
              </div>
            )}
          </div>

          {/* 执行层状态 + 生命周期记录 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-emerald-400" />
                执行层状态（Autopilot）
              </h2>
              {autopilot ? (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
                  <div className="rounded-lg border border-border/60 p-3">
                    <div className="text-[11px] text-muted-foreground">引擎存活</div>
                    <div className={cn("font-mono text-lg font-bold mt-0.5", autopilot.health.alive ? "text-emerald-400" : "text-rose-400")}>
                      {autopilot.health.alive ? "正常" : "离线"}
                    </div>
                    {autopilot.health.stale && <div className="text-[10px] text-amber-400 mt-0.5">心跳过期</div>}
                  </div>
                  <div className="rounded-lg border border-border/60 p-3">
                    <div className="text-[11px] text-muted-foreground">熔断</div>
                    <div className={cn("font-mono text-lg font-bold mt-0.5", autopilot.halt.halted ? "text-rose-400" : "text-emerald-400")}>
                      {autopilot.halt.halted ? "已触发" : "无"}
                    </div>
                    {autopilot.halt.reason && <div className="text-[10px] text-muted-foreground mt-0.5 truncate">{autopilot.halt.reason}</div>}
                  </div>
                  <div className="rounded-lg border border-border/60 p-3">
                    <div className="text-[11px] text-muted-foreground">流水线阶段</div>
                    <div className="font-mono text-lg font-bold mt-0.5">{PHASE_META[autopilot.pipeline.phase]?.label ?? autopilot.pipeline.phase}</div>
                  </div>
                  <div className="rounded-lg border border-border/60 p-3">
                    <div className="text-[11px] text-muted-foreground">今日订单</div>
                    <div className="font-mono text-lg font-bold mt-0.5">{autopilot.counter.count} <span className="text-xs text-muted-foreground">/ {autopilot.config.max_trades_per_day}</span></div>
                  </div>
                  <div className="rounded-lg border border-border/60 p-3">
                    <div className="text-[11px] text-muted-foreground">监控币对</div>
                    <div className="font-mono text-sm font-bold mt-0.5 truncate">{autopilot.config.pairs.join(", ")}</div>
                  </div>
                  <div className="rounded-lg border border-border/60 p-3">
                    <div className="text-[11px] text-muted-foreground">风控上限</div>
                    <div className="font-mono text-sm font-bold mt-0.5">${autopilot.config.max_total_exposure_usd.toLocaleString()}</div>
                  </div>
                </div>
              ) : (
                <div className="text-xs text-muted-foreground">执行层数据不可用（Autopilot 未启动或聚合失败）</div>
              )}
            </div>

            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
                <History className="h-4 w-4 text-purple-400" />
                生命周期记录
              </h2>
              {history.length ? (
                <div className="space-y-2 max-h-[220px] overflow-y-auto pr-1">
                  {history.map((ev, i) => (
                    <div key={`${ev.at}-${i}`} className="flex items-center gap-2 rounded-lg border border-border/50 px-3 py-2 text-xs">
                      <span className={cn("shrink-0 rounded-full px-2 py-0.5 font-semibold", PHASE_META[ev.phase]?.bg ?? "bg-muted", PHASE_META[ev.phase]?.color ?? "text-muted-foreground")}>
                        {PHASE_META[ev.phase]?.label ?? ev.phase}
                      </span>
                      <span className="text-muted-foreground font-mono">{ev.action}</span>
                      {ev.note && <span className="text-muted-foreground truncate">· {ev.note}</span>}
                      <span className="ml-auto text-muted-foreground/70 font-mono shrink-0">{String(ev.at).replace("T", " ").slice(5, 16)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-muted-foreground">暂无生命周期记录 — 使用右上角按钮推进策略阶段</div>
              )}
            </div>
          </div>

          {/* 执行明细: 表现 + 持仓 + 交易记录 */}
          <div className="rounded-xl border bg-card p-4">
            <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
              <Receipt className="h-4 w-4 text-cyan-400" />
              执行明细（纸面账户）
            </h2>
            {data?.autopilot_performance ? (
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
                {[
                  { label: "胜率", value: `${(data.autopilot_performance.win_rate * 100).toFixed(1)}%`, color: "text-emerald-400" },
                  { label: "已实现 PnL", value: `$${data.autopilot_performance.realized_pnl_usd.toLocaleString()}`, color: data.autopilot_performance.realized_pnl_usd >= 0 ? "text-emerald-400" : "text-rose-400" },
                  { label: "总交易", value: `${data.autopilot_performance.total_trades}` },
                  { label: "夏普", value: data.autopilot_performance.sharpe.toFixed(2) },
                  { label: "最大回撤", value: `${(data.autopilot_performance.max_drawdown * 100).toFixed(1)}%`, color: "text-rose-400" },
                ].map(k => (
                  <div key={k.label} className="rounded-lg border border-border/60 p-3">
                    <div className="text-[11px] text-muted-foreground">{k.label}</div>
                    <div className={cn("font-mono text-lg font-bold mt-0.5", k.color ?? "text-foreground")}>{k.value}</div>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div>
                <div className="text-xs text-muted-foreground mb-2">
                  当前持仓（{data?.autopilot_positions?.length ?? 0}）
                </div>
                {data?.autopilot_positions?.length ? (
                  <div className="space-y-2">
                    {data.autopilot_positions.map(p => (
                      <div key={p.symbol} className="flex items-center justify-between rounded-lg border border-border/50 px-3 py-2 text-sm">
                        <span className="font-mono font-medium">{p.symbol}</span>
                        <span className={cn("text-xs font-mono", p.side === "buy" ? "text-emerald-400" : "text-rose-400")}>
                          {p.side === "buy" ? "多" : "空"} {p.quantity}
                        </span>
                        <span className="text-xs text-muted-foreground font-mono">@{p.entry_price}</span>
                        <span className={cn("text-xs font-mono", p.unrealized_pnl >= 0 ? "text-emerald-400" : "text-rose-400")}>
                          {p.unrealized_pnl >= 0 ? "+" : ""}{p.unrealized_pnl.toFixed(2)}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground">无持仓</div>
                )}
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-2">最近交易记录</div>
                {data?.autopilot_trades?.length ? (
                  <div className="max-h-[220px] overflow-y-auto space-y-2 pr-1">
                    {data.autopilot_trades.slice(0, 12).map((t, i) => (
                      <div key={`${t.ts}-${i}`} className="flex items-center justify-between rounded-lg border border-border/50 px-3 py-2 text-xs">
                        <span className="font-mono">{t.symbol}</span>
                        <span className={cn("font-mono", t.side === "buy" ? "text-emerald-400" : "text-rose-400")}>
                          {t.side === "buy" ? "买入" : "卖出"}
                        </span>
                        <span className="font-mono text-muted-foreground">
                          {t.price != null ? t.price.toFixed(2) : "--"} × {t.quantity ?? "--"}
                        </span>
                        {t.realized_pnl != null && (
                          <span className={cn("font-mono", t.realized_pnl >= 0 ? "text-emerald-400" : "text-rose-400")}>
                            {t.realized_pnl >= 0 ? "+" : ""}{t.realized_pnl.toFixed(2)}
                          </span>
                        )}
                        <span className="text-muted-foreground/70 font-mono">{String(t.ts ?? "").slice(0, 16).replace("T", " ")}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground">暂无交易记录</div>
                )}
              </div>
            </div>
          </div>

          {/* Loop 反馈: 推荐动作 + 假设流转 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-amber-400" />
                复盘建议（Loop 反馈）
                {review?.reviewed_at && (
                  <span className="text-xs font-normal text-muted-foreground">
                    · {String(review.reviewed_at).replace("T", " ").slice(0, 16)}
                  </span>
                )}
              </h2>
              {review?.recommendations?.length ? (
                <div className="space-y-2">
                  {review.recommendations.map((r, i) => (
                    <div
                      key={`${r.level}-${i}`}
                      className={cn(
                        "flex items-start gap-2 rounded-lg border px-3 py-2 text-sm",
                        r.level === "critical" && "border-rose-500/30 bg-rose-500/5 text-rose-300",
                        r.level === "warn" && "border-amber-500/30 bg-amber-500/5 text-amber-300",
                        r.level === "info" && "border-border/60 bg-muted/30 text-muted-foreground",
                      )}
                    >
                      <span className="mt-0.5 shrink-0">
                        {r.level === "critical" ? "⛔" : r.level === "warn" ? "⚠️" : "ℹ️"}
                      </span>
                      <span>{r.text}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-muted-foreground">复盘引擎数据不可用</div>
              )}
              {review?.hypothesis_updates?.length ? (
                <>
                  <div className="text-xs text-muted-foreground mt-4 mb-2">本轮假设自动流转</div>
                  <div className="space-y-2">
                    {review.hypothesis_updates.map((u, i) => (
                      <div key={`${u.hypothesis_id}-${i}`} className="rounded-lg border border-border/50 px-3 py-2 text-xs">
                        <div className="flex items-center gap-2">
                          <span className="font-medium truncate">{u.title}</span>
                          <span className="ml-auto shrink-0 font-mono text-muted-foreground">{u.hypothesis_id}</span>
                        </div>
                        <div className="mt-1 flex items-center gap-1.5 text-muted-foreground">
                          <span className="rounded bg-muted px-1.5 py-0.5">{u.from_status}</span>
                          <ChevronRight className="h-3 w-3" />
                          <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-amber-300">{u.to_status}</span>
                          <span className="truncate">· {u.reason}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              ) : null}
              {review?.adaptations?.length ? (
                <>
                  <div className="text-xs text-muted-foreground mt-4 mb-2">参数自适应（已应用）</div>
                  <div className="space-y-2">
                    {review.adaptations.map((a, i) => (
                      <div key={`${a.param}-${i}`} className="rounded-lg border border-cyan-500/30 bg-cyan-500/5 px-3 py-2 text-xs">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-semibold text-cyan-300">{a.param}</span>
                          <span className="font-mono">{a.from_value.toFixed(2)} → {a.to_value.toFixed(2)}</span>
                          <span className="truncate text-muted-foreground">· {a.reason}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              ) : null}
              {review?.variants?.length ? (
                <>
                  <div className="text-xs text-muted-foreground mt-4 mb-2">🧬 下一代实验候选（已进 exploring）</div>
                  <div className="space-y-2">
                    {review.variants.map(v => (
                      <div key={v.hypothesis_id} className="rounded-lg border border-purple-500/30 bg-purple-500/5 px-3 py-2 text-xs">
                        <div className="flex items-center gap-2">
                          <span className="font-medium truncate">{v.title}</span>
                          <span className="ml-auto shrink-0 rounded bg-purple-500/20 px-1.5 py-0.5 text-purple-300">{v.status}</span>
                        </div>
                        <div className="mt-1 font-mono text-[10px] text-muted-foreground truncate">{v.signal_definition}</div>
                      </div>
                    ))}
                  </div>
                </>
              ) : null}
            </div>

            {/* 假设注册表 (复盘素材) */}
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
                <FlaskConical className="h-4 w-4 text-purple-400" />
                假设注册表
              </h2>
              <div className="grid grid-cols-1 gap-3">
                {hypotheses.map(h => {
                  const m = review?.variant_metrics?.[h.hypothesis_id];
                  return (
                    <div key={h.hypothesis_id} className="rounded-lg border border-border/50 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium">{h.title}</span>
                        <span
                          className={cn(
                            "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold",
                            h.status === "validated" && "bg-emerald-500/20 text-emerald-400",
                            h.status === "testing" && "bg-amber-500/20 text-amber-400",
                            h.status === "monitoring" && "bg-cyan-500/20 text-cyan-400",
                            h.status === "rejected" && "bg-rose-500/20 text-rose-400",
                            h.status === "exploring" && "bg-muted text-muted-foreground",
                          )}
                        >
                          {h.status}
                        </span>
                      </div>
                      {h.thesis && <div className="mt-1 text-xs text-muted-foreground line-clamp-2">{h.thesis}</div>}
                      {m && (
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
                          <span className={cn("rounded bg-muted px-1.5 py-0.5", (m.annual ?? 0) > 0 ? "text-emerald-400" : "text-rose-400")}>
                            回测 {m.annual != null ? `${m.annual > 0 ? "+" : ""}${m.annual}%` : "--"}
                          </span>
                          <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground">夏普 {m.sharpe ?? "--"}</span>
                          <span className="rounded bg-muted px-1.5 py-0.5 text-rose-400">回撤 {m.max_dd ?? "--"}%</span>
                          {h.status === "testing" && (
                            <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-amber-300">✅ 通过晋升，可进模拟</span>
                          )}
                        </div>
                      )}
                      <div className="mt-1.5 font-mono text-[10px] text-muted-foreground">{h.hypothesis_id}</div>
                    </div>
                  );
                })}
                {!hypotheses.length && <div className="text-xs text-muted-foreground">暂无假设记录</div>}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
