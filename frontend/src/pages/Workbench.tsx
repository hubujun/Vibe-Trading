import { useEffect, useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  ChevronDown,
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
  type ComboHypothesis,
  type WorkbenchResponse,
  type WorkbenchReview,
  type WorkbenchStrategy,
} from "@/lib/api";
import { echarts } from "@/lib/echarts";
import { useThemeDark } from "@/lib/theme-store";
import { cn } from "@/lib/utils";
import { backtestValue } from "@/lib/backtestDisplay";

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

// 学术因子池 (academic zoo 全部 13 个, 与 variant_backtester.ACADEMIC_MODULES 对齐)
const ACADEMIC_FACTORS = [
  { id: "BAB", name: "低贝塔", desc: "Frazzini-Pedersen 低贝塔溢价, 组合基座" },
  { id: "high52w", name: "52周高点", desc: "George-Hwang 52周高点动量, 组合基座" },
  { id: "RMW", name: "盈利", desc: "Fama-French 盈利能力(价格代理), 已评估" },
  { id: "carhart_mom", name: "动量", desc: "Carhart UMD 252-21 日动量" },
  { id: "strev", name: "短期反转", desc: "21 日收益反转" },
  { id: "illiq", name: "非流动性", desc: "Amihud 非流动性, 负 IC 反向使用" },
  { id: "smb", name: "小市值", desc: "Fama-French 规模因子, 负 IC 反向使用" },
  { id: "hml", name: "价值", desc: "Fama-French 账面市值比" },
  { id: "cma", name: "投资", desc: "Fama-French 投资因子" },
  { id: "retskew", name: "收益偏度", desc: "60 日收益偏度反转" },
  { id: "mkt_rf", name: "市场", desc: "21 日市场收益" },
  { id: "corr_rewire", name: "相关性重构", desc: "横截面相关性重构" },
];

function fmtPct(v: number | undefined | null, signed = true): string {
  if (v == null || Number.isNaN(v)) return "--";
  return `${signed && v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

// ---------------------------------------------------------------------------
// 新手名词提示 (量化小白友好)
// ---------------------------------------------------------------------------

/** 关键术语 → 白话解释 (hover 显示). */
const TERMS: Record<string, string> = {
  sharpe: "夏普比率：每承担 1 份风险能换多少超额收益。越高越好，>1 算优秀，0.79 属中等偏上",
  ic: "IC 信息系数：因子预测方向与真实涨跌的相关系数，范围 -1~1。>0.05 算有预测力，接近 0 就是没用的因子",
  ir: "IR 信息比率：IC 均值 ÷ IC 波动，衡量预测力稳不稳定。>0.1 说明不是碰运气",
  icPos: "IC+ 比率：IC 为正的天数占比。>50% 说明因子大多数时候方向是对的",
  annual: "回测年化：把回测期总收益折算成每年收益（复利口径）。+12.77% = 每年平均赚 12.77%",
  maxDd: "最大回撤：账户从最高点跌到最低点的最大跌幅。-10.62% = 最惨的时候亏了 10.62%",
  cum: "累计收益：整个回测期的总收益",
  nav: "净值：模拟盘账户价值，1.0 = 初始资金，>1.0 赚钱，<1.0 亏钱",
  rebalance: "调仓：按固定周期（这里每天）重新计算信号、买卖换仓",
  weight: "权重：各因子在组合评分里的占比。BAB 50% = 组合得分一半来自 BAB 因子",
  topBot: "多 top3 空 bottom3：每天把 10 个币按因子得分排序，最高的 3 个做多、最低的 3 个做空",
  winRate: "胜率：赚钱的交易占比。50% 胜率配合高盈亏比也能赚钱",
  pf: "Profit Factor 盈亏比：总盈利 ÷ 总亏损。>1 才赚钱，>1.5 算健康",
  pnl: "已实现 PnL：平仓后真正落袋的盈亏（浮盈浮亏不算）",
  leverage: "杠杆/仓位乘子：实际下注比例。1.0 = 满仓按信号做，0.5 = 只下 5 成仓（风控触发时自动降）",
  exposure: "仓位乘子：见上方杠杆说明。复盘引擎检测到回撤/连亏会自动降低它来控制风险",
  zscore: "z-score 标准化：把因子原始值换算成'偏离平均值几个标准差'，让不同因子可以公平相加",
  crossSection: "横截面：同一天内多个币之间横向比较（比如今天 10 个币里谁得分最高），不是看单个币的时间走势",
  bab: "BAB 低贝塔因子：做多波动小的币、做空波动大的币，赚'低波动溢价'。学术文献里的经典因子",
  high52w: "52 周高点因子：离 52 周最高点越近（强势）的币越可能继续涨，动量效应",
  rmw: "RMW 盈利因子：盈利能力强的公司/币表现更好（用价格波动代理）",
  exploring: "探索中：变体刚生成，等待回测验证",
  testing: "验证中：已通过回测，等待模拟盘验证",
  validated: "已验证：模拟盘表现合格，可上线",
  rejected: "已否决：验证失败（连亏/回撤超限），不再使用",
  monitoring: "monitoring 观察中：曾经合格但现在回撤超限，降级观察",
  ddBreach: "回撤超限：模拟盘当前回撤超过回测最大回撤的 1.5 倍，触发风控（降杠杆/建议回炉）",
  sampleShort: "样本不足：交易记录少于 20 笔，统计上还不能下结论，继续积累",
  loopNext: "下一圈：复盘后螺旋的去向。回组合 = 继续迭代变体；回研究 = 表现差回炉重做",
  signalScore: "因子得分：综合所有因子的加权评分，越高越看多",
  phaseMine: "挖掘：factor miner 自动挖新因子，过三关（Monte Carlo 置换 / Bootstrap 夏普 / walk-forward）才准用。全局共享环节",
  phaseCompose: "组合：变体生成器把因子拼成候选组合（调权重 / 三因子 / 加入新因子），自动回测对比基策略，跑赢双超才晋升。全局共享环节",
  phaseResearch: "研究：回测验证阶段。候选组合用 800 天历史数据回测（年化/夏普/回撤达标），才允许进模拟盘",
  phasePaper: "模拟：模拟盘用真实行情记账（含 0.1% 交易成本和永续资金费），积累 20 笔调仓后复盘才有统计意义",
  phaseLive: "执行：autopilot 执行层。当前为纸面账户模拟执行（paper_trading），未接真实交易所",
  phaseReview: "复盘：每日体检模拟盘表现（vs 回测 / 回撤超限 / 信号新鲜度），输出建议并决定下一圈去向（回组合迭代 / 回研究回炉）",
  navTrack: "模拟盘净值追踪：该策略模拟盘账户的净值曲线，每个调仓点更新。净值从 1.0 起步，>1 赚钱 <1 亏钱",
  factorTiers: "因子分层：学术(文献因子) → 挖掘(factor miner 自动挖) → 组合(变体拼装)。下层供上层，每层有自己的 IC 表和状态标注",
  autopilotStatus: "执行层状态（Autopilot）：策略信号的实际执行引擎。当前 paper_trading = 纸面模拟执行，未接真实交易所",
  lifecycleLog: "流水线生命周期记录：策略在流水线里的阶段变更履历（挖掘→组合→研究→模拟→执行→复盘 每次迁移都有记录），可追溯它怎么一步步走到今天",
  crazyBull: "疯牛保险：当全场普涨（>50% 币 20 天涨超 15% 且 BTC 也在涨）时，市场分化小、空头容易被轧，系统自动把杠杆降到 ×0.4 避险。普涨退潮后自动恢复满杠杆。2021 疯牛回测 -50% 回撤就是靠它砍到 -35%",
  execDetail: "执行明细（纸面账户）：纸面账户的交易统计（胜率/PnL/夏普/回撤）+ 当前持仓。样本少时数字仅供参考",
  reviewAdvice: "复盘建议（Loop 反馈）：复盘引擎给策略的体检报告和建议（下一圈去组合迭代还是回研究回炉），每日 08:30 更新",
  hypothesisReg: "候选组合：所有因子假设（学术/挖掘）与变体组合的状态档案。exploring→testing→validated 是晋升路径，rejected 是被否决的。每次回测跑赢基策略的组合自动晋升并播种进模拟盘",
  lifecycle: "生命周期：因子在矿机-验证体系里的状态。活跃=已过三关在交易，退役=被三关拒绝，候选=待审判",
  tradeCount: "交易数：该因子实际参与的交易笔数，样本越多统计越可信",
  signalExplain: "今日信号 = 在 15 个币里做横截面打分（z-score 标准化）：得分最高 3 个做多（预期相对强势），最低 3 个做空（预期相对弱势）。注意：这是相对强弱排名，不是涨跌预测——普涨行情里做空的币也可能上涨，普跌行情里做多的币也可能下跌。",
  fundingHedge: "永续资金费：OKX 每 8 小时结算一次，多头付给空头（正费率时）。多空各 3 仓位时，多头付的被空头收的抵消（净≈0）——这是市场中性对冲组合在永续市场的红利：既对冲了跌价风险，又省掉了纯多头持仓的资金费成本（0.03%/天 × 3 ≈ 年化 33%）。",
  btCompare: "回测对比：800 天历史数据 + 交易成本模拟的结果。COMBO2 = BAB+52周双因子，COMBO3 = 三因子",
  oos: "OOS 样本外：没用过的数据（未来数据），防过拟合的关键",
};

//: 假设状态 → 中文标签 (UI 展示)
const STATUS_LABEL: Record<string, string> = {
  exploring: "探索中",
  testing: "验证中",
  validated: "已验证",
  monitoring: "观察中",
  rejected: "已否决",
};

//: 执行层 (autopilot) 流水线阶段 → 中文标签
const PIPELINE_PHASE_LABEL: Record<string, string> = {
  idle: "待机",
  collecting: "收集行情",
  discovering: "发现机会",
  backtesting: "回测",
  paper_trading: "纸面交易",
  live: "真实交易",
  feedback: "反馈",
};

/** 术语徽标: 词 + 问号, hover 显示白话解释. */
function Term({ k, children }: { k: keyof typeof TERMS | string; children?: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-0.5 group/term">
      {children}
      <span
        className="inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full border border-border text-[9px] text-muted-foreground group-hover/term:text-cyan-400"
        title={TERMS[k] ?? k}
      >
        ?
      </span>
    </span>
  );
}

export function Workbench() {
  const [data, setData] = useState<WorkbenchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [tier, setTier] = useState<"academic" | "mined" | "combo">("academic");
  const [selectedId, setSelectedId] = useState<string>(() => {
    try { return window.localStorage.getItem("qa-wb-strategy") ?? ""; } catch { return ""; }
  });
  // 阶段浏览: 中间显示当前查看的阶段, 左右相邻可点击切换 (默认跟随策略当前阶段)
  const [viewStage, setViewStage] = useState<string>("research");
  // 详情面板: 全部默认展开 — 阶段浏览到哪个阶段, 对应详情直接展示全貌 (可手动收起)
  const [openDetails, setOpenDetails] = useState<Record<string, boolean>>({ research: true, exec: true, registry: true });
  // 流水线生命周期记录: 默认折叠 (履历是低频查看内容)
  const [openLifecycle, setOpenLifecycle] = useState(false);
  const dark = useThemeDark();
  // 今日宏观事件 (第 1 层: 事件日历, 有事件才渲染)
  const macroEvents = data?.macro?.events ?? [];

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

  // 综合排序 (有信号优先 → 净值高优先 → 调仓样本多优先) — select options 与默认选中共用
  const sortedStrategies = useMemo(() => {
    if (!data?.strategies) return [] as WorkbenchStrategy[];
    return [...data.strategies].sort((a, b) => {
      const na = a.paper?.nav ?? 1, nb = b.paper?.nav ?? 1;
      const ta = a.paper?.trades?.length ?? 0, tb = b.paper?.trades?.length ?? 0;
      const da = a.paper?.last_signal_date ?? "", db = b.paper?.last_signal_date ?? "";
      if (!!da !== !!db) return da ? -1 : 1;
      if (na !== nb) return nb - na;
      if (ta !== tb) return tb - ta;
      return String(a.strategy_id).localeCompare(String(b.strategy_id));
    });
  }, [data]);

  const strategy: WorkbenchStrategy | undefined =
    sortedStrategies.find(s => s.strategy_id === selectedId) ?? sortedStrategies[0];
  // 默认选中排序第一个 (无持久化选择时); 选择持久化到 localStorage
  useEffect(() => {
    if (!selectedId && sortedStrategies.length) setSelectedId(sortedStrategies[0].strategy_id);
  }, [data, selectedId]);
  // 切换策略时, 阶段浏览重置为该策略当前所处阶段
  useEffect(() => {
    const ph = strategy?.phase === "paused" ? "research" : (strategy?.phase ?? "research");
    setViewStage(ph);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy?.strategy_id]);
  const phase = strategy?.phase ?? "research";
  const combo2 = data?.combo.metrics.backtest["COMBO2(BAB+52w)"];
  const autopilot = data?.autopilot;
  const hypotheses = data?.combo.hypotheses ?? [];
  // 复盘: 策略自身体检 + 全局组合层数据 (variants/variant_metrics/hypothesis_updates)
  const review = {
    ...(data?.review ?? {}),
    ...(strategy?.review ?? {}),
  } as WorkbenchReview;
  const paper: WorkbenchStrategy["paper"] = strategy?.paper ?? {};

  // --- 播种变体为新策略 (多策略并行) ---
  const seededDefs = new Set((data?.strategies ?? []).map(s => s.signal_definition));
  const seedVariant = async (h: ComboHypothesis) => {
    if (!h.signal_definition || mutating) return;
    setMutating(true);
    try {
      const name = (h.title ?? "").replace(/^基策略 /, "").slice(0, 36) || "变体策略";
      const created = await api.seedStrategy(h.signal_definition, name);
      toast.success(`已播种并行策略: ${created.name}`);
      await load();
      setSelectedId(created.strategy_id);
    } catch (e) {
      toast.error((e as { detail?: string })?.detail ?? "播种失败");
    } finally {
      setMutating(false);
    }
  };

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
    const trades = paper?.trades?.length ? paper.trades : data?.combo.paper.trades;
    if (!trades?.length) return null;
    let nav = paper?.nav ?? data?.combo.paper.nav ?? 1;
    const reversed: { d: string; v: number }[] = [];
    for (let i = trades.length - 1; i >= 0; i--) {
      reversed.unshift({ d: trades[i].to, v: nav });
      nav = nav / (1 + trades[i].ret / 100);
    }
    reversed.unshift({ d: trades[0].from, v: nav });
    const byDate = new Map<string, { d: string; v: number }>();
    for (const p of reversed) byDate.set(p.d, p);
    return Array.from(byDate.values());
  }, [data, paper]);

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

  // 挖掘层候选: zoo 因子 − 活跃 − 退役 (退役 alpha_id 带 crypto_mined_ 前缀, 归一化比较)
  const normFactorId = (s: string) => s.replace(/^crypto_mined_/, "");
  const minedCandidates = (data?.autopilot_factors?.zoo ?? []).filter(z => {
    const retired = data?.autopilot_factors?.retired ?? [];
    return (
      !(data?.autopilot_factors?.active ?? []).some(a => normFactorId(a.alpha_id) === normFactorId(z.alpha_id)) &&
      !retired.some(r => normFactorId(r.alpha_id) === normFactorId(z.alpha_id))
    );
  });
  // 退役唯一因子数 (记录可能重复: 同一因子被三关多次拒绝)
  const retiredUniqueCount = new Set((data?.autopilot_factors?.retired ?? []).map(r => normFactorId(r.alpha_id))).size;

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

  const stageStats: Record<string, { label: React.ReactNode; value: string; color?: string }[]> = {
    mine: [
      { label: "zoo 因子", value: `${data?.autopilot_factors?.zoo_count ?? "--"}` },
      { label: "活跃（交易中）", value: `${data?.autopilot_factors?.active?.length ?? "--"}`, color: "text-emerald-400" },
      { label: <Term k="exploring">候选（未审判）</Term>, value: `${minedCandidates.length}`, color: "text-amber-400" },
      { label: "退役（唯一因子）", value: `${retiredUniqueCount}`, color: "text-muted-foreground" },
    ],
    compose: [
      { label: "变体候选", value: `${variantCount}`, color: "text-purple-400" },
      { label: "已自动回测", value: `${backtestedCount}`, color: "text-cyan-400" },
      { label: <Term k="testing">晋升验证中</Term>, value: `${testingCount}`, color: "text-amber-400" },
      { label: "自动回测", value: "每日 08:45" },
    ],
    research: [
      // 选中策略自己的回测指标 (播种策略来自变体回测缓存, 基策略回退 COMBO2)
      // 2026-08-30 修复: error 标记 (僵尸/不可用策略) 显示 "--" 而不是 fallback
      // combo2 — 之前 cum=null fallback 导致多条策略显示相同 30.13% (事故)
      { label: <Term k="annual">回测年化</Term>, value: backtestValue(strategy?.strategy_backtest, "annual", combo2?.annual) != null ? fmtPct(backtestValue(strategy?.strategy_backtest, "annual", combo2?.annual) as number) : "--", color: "text-emerald-400" },
      { label: <Term k="sharpe">回测夏普</Term>, value: backtestValue(strategy?.strategy_backtest, "sharpe", combo2?.sharpe) != null ? (backtestValue(strategy?.strategy_backtest, "sharpe", combo2?.sharpe) as number).toFixed(2) : "--" },
      { label: <Term k="maxDd">最大回撤</Term>, value: backtestValue(strategy?.strategy_backtest, "max_dd", combo2?.max_dd) != null ? fmtPct(backtestValue(strategy?.strategy_backtest, "max_dd", combo2?.max_dd) as number) : "--", color: "text-rose-400" },
      { label: <Term k="cum">累计收益</Term>, value: backtestValue(strategy?.strategy_backtest, "cum", combo2?.cum) != null ? fmtPct(backtestValue(strategy?.strategy_backtest, "cum", combo2?.cum) as number) : "--", color: "text-emerald-400" },
    ],
    paper: [
      { label: <Term k="nav">模拟盘净值</Term>, value: paper?.nav != null ? paper.nav.toFixed(4) : "--", color: "text-emerald-400" },
      { label: <Term k="rebalance">调仓次数</Term>, value: `${paper?.trades?.length ?? 0}` },
      { label: "起于", value: paper?.started_at ? String(paper.started_at).slice(0, 10) : "--" },
      { label: "最新信号", value: paper?.last_signal_date ?? "--", color: "text-amber-400" },
    ],
    live: [
      { label: "引擎存活", value: autopilot?.health.alive ? "是" : "否", color: autopilot?.health.alive ? "text-emerald-400" : "text-rose-400" },
      { label: "熔断", value: autopilot?.halt.halted ? "已触发" : "无", color: autopilot?.halt.halted ? "text-rose-400" : "text-emerald-400" },
      { label: "今日订单", value: autopilot ? `${autopilot.counter.count} / ${autopilot.config.max_trades_per_day}` : "--" },
      { label: "流水线", value: autopilot ? (PIPELINE_PHASE_LABEL[autopilot.pipeline.phase] ?? autopilot.pipeline.phase) : "--" },
    ],
    review: [
      { label: "vs 回测", value: review?.vs_backtest?.outperforming == null ? "样本不足" : review.vs_backtest!.outperforming ? "跑赢" : "跑输", color: review?.vs_backtest?.outperforming ? "text-emerald-400" : review?.vs_backtest?.outperforming == null ? undefined : "text-rose-400" },
      { label: <Term k="ddBreach">回撤超限</Term>, value: review?.vs_backtest?.dd_breach ? "是" : "否", color: review?.vs_backtest?.dd_breach ? "text-rose-400" : "text-emerald-400" },
      { label: "信号新鲜度", value: review?.signal_health?.stale ? "过期" : "正常", color: review?.signal_health?.stale ? "text-amber-400" : "text-emerald-400" },
      { label: <Term k="loopNext">下一圈</Term>, value: review?.loop_next === "research" ? "回研究(回炉)" : "回组合(迭代)", color: review?.loop_next === "research" ? "text-rose-400" : "text-purple-400" },
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

      {/* Strategy selector: 下拉选择框, 按综合效果排序 (有信号优先 → 净值 → 调仓样本) */}
      {data?.strategies.length ? (
        <div className="flex flex-wrap items-center gap-2">
          {data.strategies.length > 1 ? (
            <>
              <span className="text-xs text-muted-foreground">策略:</span>
              <select
                value={selectedId || sortedStrategies[0]?.strategy_id || ""}
                onChange={e => {
                  setSelectedId(e.target.value);
                  try { window.localStorage.setItem("qa-wb-strategy", e.target.value); } catch { /* ignore */ }
                }}
                className="h-8 min-w-[280px] max-w-[640px] rounded-lg border border-border bg-card px-3 text-xs font-medium text-foreground outline-none hover:border-cyan-500/40 focus:border-cyan-500/50"
                title="按综合效果排序: 有信号优先 → 净值高优先 → 调仓样本多优先"
              >
                {sortedStrategies.map(s => {
                    const nav = s.paper?.nav != null ? s.paper.nav.toFixed(4) : "--";
                    const nt = s.paper?.trades?.length ?? 0;
                    const sig = s.paper?.last_signal_date ? ` · 信号${s.paper.last_signal_date}` : " · 待首个信号";
                    return (
                      <option key={s.strategy_id} value={s.strategy_id}>
                        {s.name} — 净值 {nav} · {nt}次调仓{sig}
                      </option>
                    );
                  })}
              </select>
              <span className="text-xs text-muted-foreground opacity-70">
                共 {data.strategies.length} 条 · 按综合效果排序
              </span>
            </>
          ) : (
            <span className="text-xs text-muted-foreground">
              当前 1 条策略 — 组合层变体可播种为新策略并行运行
            </span>
          )}
        </div>
      ) : null}

      {/* Strategy identity */}
      {strategy && (
        <div className="rounded-xl border bg-card p-4">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-sm font-semibold">{strategy.name}</span>
            <span className={cn("rounded-full px-2.5 py-0.5 text-xs font-semibold", PHASE_META[effectivePhase].bg, PHASE_META[effectivePhase].color)}>
              {isPaused ? "已暂停" : PHASE_META[effectivePhase].label}
            </span>
            <span className="inline-flex items-center gap-1 rounded-full border border-violet-500/30 bg-violet-500/10 px-2.5 py-0.5 text-xs font-medium text-violet-500">
              横截面相对价值
              <Term k="crossSection" />
            </span>
            <span className="text-xs text-muted-foreground">
              因子: {strategy.factors.join(" + ")} · 权重: {Object.entries(strategy.weights).map(([k, v]) => `${k} ${v * 100}%`).join(" / ")}
            </span>
            <span className="text-xs text-muted-foreground">{strategy.universe_size} 币 · {strategy.rebalance}</span>
            <span className="text-xs text-muted-foreground">
              <Term k="leverage">杠杆 ×{strategy.params?.exposure_multiplier ?? 1.0}</Term>
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
                          "relative flex h-11 w-11 items-center justify-center rounded-full border-2 transition-all",
                          active ? cn(meta.border, meta.bg, "scale-110") : passed ? "border-cyan-500/50 bg-cyan-500/10" : "border-border bg-muted/40",
                        )}
                      >
                        <Icon className={cn("h-5 w-5", active ? meta.color : passed ? "text-cyan-400" : "text-muted-foreground")} />
                        {p === "review" && (passed || active) && (
                          <span className="absolute -top-1.5 -right-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-purple-500/40 text-[10px] font-bold text-purple-200">
                            ↺
                          </span>
                        )}
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
            {/* 循环点说明: 复盘之后去哪 */}
            <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border/60 pt-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <RefreshCw className="h-3 w-3 text-purple-400" />
                循环点: 复盘 → 组合 — 基策略有效时自动生成下一代变体
              </span>
              {/* 今日宏观事件: 第 1 层事件风控 — 事件日自动降杠杆 */}
              <span className="flex items-center gap-1.5 border-l border-border/60 pl-4">
                <span className={cn("rounded px-1.5 py-0.5 font-semibold", macroEvents.length ? (macroEvents.some(e => e.level === "A") ? "bg-rose-500/20 text-rose-400" : "bg-amber-500/20 text-amber-400") : "bg-muted text-muted-foreground")}>
                  {macroEvents.length ? (macroEvents.some(e => e.level === "A") ? "重大" : "事件") : "今日"}
                </span>
                {macroEvents.length ? (
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    {macroEvents.map((ev, i) => (
                      <span key={i} className="flex items-center gap-1.5">
                        <span className="text-foreground/80">{ev.title}</span>
                        {(data?.macro?.event_multiplier ?? 1) < 1 && (
                          <span className="text-rose-400">→ 杠杆 ×{data?.macro?.event_multiplier}</span>
                        )}
                      </span>
                    ))}
                  </span>
                ) : (
                  <span className="text-muted-foreground/70">无重大事件 · 满杠杆运行</span>
                )}
              </span>
              {/* 疯牛保险: 普涨环境自动降仓 (第 4 层风控, 与回测一致) */}
              <span className="flex items-center gap-1.5 border-l border-border/60 pl-4">
                <span className={cn("rounded px-1.5 py-0.5 font-semibold", (data?.macro?.crazy_mult ?? 1) < 1 ? "bg-rose-500/20 text-rose-400" : "bg-muted text-muted-foreground")}>
                  疯牛保险
                </span>
                {(data?.macro?.crazy_mult ?? 1) < 1 ? (
                  <span className="flex items-center gap-1 text-rose-400">
                    普涨降仓 → 杠杆 ×{data?.macro?.crazy_mult} <Term k="crazyBull" />
                  </span>
                ) : (
                  <span className="text-muted-foreground/70">未触发</span>
                )}
              </span>
              <span className="flex items-center gap-1">
                <Undo2 className="h-3 w-3 text-amber-400" />
                复盘 → 研究 — 回撤超限 / 连亏 3 笔时回炉
              </span>
              <span className={cn("ml-auto font-medium", review?.loop_next === "research" ? "text-rose-400" : "text-purple-400")}>
                {review?.loop_next === "research" ? "⚠️ 当前下一圈: 回研究" : "↺ 当前下一圈: 回组合"}
              </span>
            </div>
          </div>

          {/* 流水线生命周期记录: 策略阶段履历 — 默认折叠, 点击展开 */}
          <div className="rounded-xl border bg-card overflow-hidden">
            <button
              onClick={() => setOpenLifecycle(o => !o)}
              className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold hover:bg-accent/50 transition-colors">
              <span className="flex items-center gap-2"><History className="h-4 w-4 text-purple-400" />流水线生命周期记录 <Term k="lifecycleLog" /></span>
              <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform", openLifecycle && "rotate-180")} />
            </button>
            {openLifecycle && (
              <div className="px-4 pb-4">
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
                <div className="text-xs text-muted-foreground">暂无流水线生命周期记录 — 使用右上角按钮推进策略阶段</div>
              )}
              </div>
            )}
          </div>

          {/* Stage cards: 左右切换浏览 (中间=当前查看阶段, 左右=相邻阶段可点击) */}
          {(() => {
            const effectivePhase = strategy?.phase === "paused" ? "research" : (strategy?.phase ?? "research");
            const currentIdx = Math.max(0, PHASE_ORDER.indexOf(effectivePhase));
            const viewIdx = Math.max(0, PHASE_ORDER.indexOf(viewStage));
            const prevStage = viewIdx > 0 ? PHASE_ORDER[viewIdx - 1] : null;
            const nextStage = viewIdx < PHASE_ORDER.length - 1 ? PHASE_ORDER[viewIdx + 1] : null;

            const renderCard = (p: string, size: "lg" | "sm", onClick?: () => void) => {
              const meta = PHASE_META[p];
              const stats = stageStats[p] ?? [];
              const st = nodeStatus(p, currentIdx);
              const active = st === "current";
              const passed = st === "passed";
              const isCenter = size === "lg";
              return (
                <div
                  key={p}
                  onClick={onClick}
                  className={cn(
                    "rounded-xl border bg-card transition-all",
                    isCenter
                      ? cn("p-4", active ? cn(meta.border, "ring-1 ring-current/10") : passed ? "border-cyan-500/30" : "border-border", "shadow-lg")
                      : "p-2.5 opacity-75 hover:opacity-100 hover:border-cyan-500/40 cursor-pointer border-border",
                  )}
                >
                  <div className={cn("flex items-center gap-2 mb-2", !isCenter && "mb-1")}>
                    <meta.icon className={cn("h-4 w-4 shrink-0", active ? meta.color : passed ? "text-cyan-400" : "text-muted-foreground")} />
                    <h2 className={cn("font-semibold", isCenter ? "text-sm" : "text-xs")}>{meta.label}</h2>
                    <Term k={`phase${p[0].toUpperCase()}${p.slice(1)}`} />
                    {(p === "mine" || p === "compose") && (
                      <span className="hidden lg:inline rounded-full bg-muted px-1.5 py-0.5 text-[9px] font-medium text-muted-foreground" title="挖掘与组合是全局供给环节 — 所有策略共享同一因子池/变体池, 不随选中策略变化">全局</span>
                    )}
                    {isCenter && viewStage === strategy?.phase && (
                      <span className={cn("ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold", meta.bg, meta.color)}>当前</span>
                    )}
                    {isCenter && viewStage !== strategy?.phase && (
                      <button
                        onClick={e => { e.stopPropagation(); setViewStage(strategy?.phase ?? "research"); }}
                        className="ml-auto rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground hover:bg-accent"
                        title="回到策略当前所处阶段"
                      >
                        回当前
                      </button>
                    )}
                    {!isCenter && passed && p !== "review" && <span className="ml-auto text-[10px] text-cyan-400">✓</span>}
                  </div>
                  <div className={cn("grid grid-cols-2 gap-2", isCenter ? "gap-3" : "")}>
                    {(isCenter ? stats : stats.slice(0, 2)).map((s, si) => (
                      <div key={`${si}-${String(s.label).slice(0, 8)}`}>
                        <div className={cn("text-muted-foreground", isCenter ? "text-[11px]" : "text-[10px]")}>{s.label}</div>
                        <div className={cn("font-mono font-bold mt-0.5", isCenter ? "text-lg" : "text-sm", s.color ?? "text-foreground")}>{s.value}</div>
                      </div>
                    ))}
                  </div>
                  {isCenter && p === "compose" && strategy && (
                    <div className="mt-3 border-t border-border/50 pt-2 text-[11px] text-muted-foreground leading-relaxed">
                      当前策略: <span className="text-foreground">{strategy.name}</span>
                      <span className="ml-2 font-mono">
                        回测 {strategy?.strategy_backtest?.annual != null ? `${strategy.strategy_backtest.annual}%` : "--"}
                        / 夏普 {strategy?.strategy_backtest?.sharpe?.toFixed(2) ?? "--"}
                        / 回撤 {strategy?.strategy_backtest?.max_dd ?? "--"}%
                      </span>
                    </div>
                  )}
                  {isCenter && p === "review" && (
                    <div className="mt-3 border-t border-border/50 pt-2 text-[11px] text-muted-foreground">
                      ↺ 下一圈: <span className="text-foreground">{review?.loop_next === "research" ? "回研究(回炉)" : "回组合(迭代)"}</span>
                    </div>
                  )}
                </div>
              );
            };

            return (
              <>
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xs text-muted-foreground">
                    阶段浏览 · 中间为当前查看阶段，点击左右卡片切换
                  </span>
                  <span className="text-xs text-muted-foreground opacity-60">
                    {viewIdx + 1} / {PHASE_ORDER.length}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex-[0.75] min-w-0">
                    {prevStage ? renderCard(prevStage, "sm", () => setViewStage(prevStage)) : <div className="rounded-xl border border-dashed border-border/50 p-2.5 py-4 flex items-center justify-center text-[10px] text-muted-foreground opacity-50">已到流水线起点</div>}
                  </div>
                  <div className="flex-[1.25] min-w-0">
                    {renderCard(viewStage, "lg")}
                  </div>
                  <div className="flex-[0.75] min-w-0">
                    {nextStage ? renderCard(nextStage, "sm", () => setViewStage(nextStage)) : <div className="rounded-xl border border-dashed border-border/50 p-2.5 py-4 flex items-center justify-center text-[10px] text-muted-foreground opacity-50">已到流水线终点</div>}
                  </div>
                </div>
              </>
            );
          })()}

          {/* Today signal + NAV */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
                今日信号
                {paper?.last_signal_date && (
                  <span className="text-xs font-normal text-muted-foreground">({paper.last_signal_date})</span>
                )}
                <Term k="signalExplain" />
              </h2>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-xs text-emerald-400 mb-2 flex items-center gap-1">
                    <ArrowUpRight className="h-3.5 w-3.5" /> 做多 <span className="text-[10px] text-muted-foreground font-normal">得分最高 5 个</span>
                  </div>
                  {(paper?.scores ? Object.entries(paper.scores).sort((a, b) => b[1] - a[1]).slice(0, 5) : (paper?.longs ?? []).map(s => [s, 0]) as [string, number][]).map(([sym, sc]) => (
                    <div key={sym} className="mb-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2.5">
                      <div className="flex justify-between items-center">
                        <span className="font-mono text-sm font-semibold">{sym}</span>
                        <span className="text-xs text-emerald-400 font-mono" title={TERMS.signalScore}>{sc?.toFixed(2) ?? "--"}</span>
                      </div>
                      <div className="mt-1.5 h-1.5 rounded-full bg-muted overflow-hidden">
                        <div className="h-full bg-emerald-400" style={{ width: `${Math.min(100, Math.max(8, ((sc ?? 0) + 2) * 30))}%` }} />
                      </div>
                    </div>
                  ))}
                  {!(paper?.scores) && !(paper?.longs?.length) && <div className="text-xs text-muted-foreground">等待每日信号…</div>}
                </div>
                <div>
                  <div className="text-xs text-rose-400 mb-2 flex items-center gap-1">
                    <ArrowDownRight className="h-3.5 w-3.5" /> 做空 <span className="text-[10px] text-muted-foreground font-normal">得分最低 3 个</span>
                  </div>
                  {(paper?.shorts ?? []).map(sym => (
                    <div key={sym} className="mb-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-2.5">
                      <div className="flex justify-between items-center">
                        <span className="font-mono text-sm font-semibold">{sym}</span>
                        <span className="text-xs text-rose-400 font-mono">{paper?.scores?.[sym]?.toFixed(2) ?? "--"}</span>
                      </div>
                      <div className="mt-1.5 h-1.5 rounded-full bg-muted overflow-hidden">
                        <div className="h-full bg-rose-400" style={{ width: `${Math.min(100, Math.max(8, (Math.abs(paper?.scores?.[sym] ?? 0) + 0.5) * 30))}%` }} />
                      </div>
                    </div>
                  ))}
                  {!(paper?.shorts?.length) && <div className="text-xs text-muted-foreground">等待每日信号…</div>}
                </div>
              </div>
              <div className="mt-3 border-t border-border/50 pt-2 text-[10px] text-muted-foreground leading-relaxed">
                得分 = 多因子加权打分（横截面 z-score，正=相对强势，负=相对弱势）· 每日 07:00 更新 · 信号是相对强弱排名，不是涨跌预测
              </div>
            </div>

            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">模拟盘净值追踪 <Term k="navTrack" /></h2>
              {navChart ? (
                <div id="workbench-nav-chart" className="h-[220px] w-full" />
              ) : (
                <div className="flex h-[220px] items-center justify-center text-xs text-muted-foreground">
                  净值曲线将在积累 2+ 次调仓后显示
                </div>
              )}
              <div className="mt-2 text-xs text-muted-foreground">
                {paper?.trades?.length ?? 0} 次调仓记录 · 每日 07:00 自动更新
                {paper?.equity_usd != null && (
                  <span className="ml-2 font-mono text-emerald-400">
                    模拟金额 {paper.equity_usd.toFixed(2)}U (初始 {paper.initial_funding_usd?.toFixed(0) ?? 1500}U)
                  </span>
                )}
              </div>
              {(() => {
                const trades = paper?.trades ?? [];
                if (!trades.length) return null;
                const fund = trades.reduce(
                  (acc, t) => {
                    acc.paid += t.funding_paid ?? 0;
                    acc.received += t.funding_received ?? 0;
                    acc.net += t.funding_net ?? 0;
                    acc.days += 1;
                    return acc;
                  },
                  { paid: 0, received: 0, net: 0, days: 0 },
                );
                // 对比: 若纯多头持仓 (3 仓位), 同期资金费成本
                const longOnlyCost = -(fund.paid / (fund.days || 1)) * (fund.days || 1);
                return (
                  <div className="mt-2 border-t border-border/50 pt-2 text-[10px] text-muted-foreground leading-relaxed">
                    永续资金费: 多头付 <span className="text-rose-400 font-mono">{fund.paid.toFixed(2)}%</span> / 空头收 <span className="text-emerald-400 font-mono">{fund.received.toFixed(2)}%</span> / 净 <span className="font-mono">{fund.net.toFixed(2)}%</span>
                    <span title="多空各 3 仓位时, 多头付的资金费被空头收的资金费抵消 — 市场中性组合在永续市场的红利; 纯多头持仓同期成本 ≈ 0.03%/天 × 3">
                      <Term k="fundingHedge" />
                    </span>
                    {fund.net.toFixed(2) !== "0.00" && (
                      <span className="text-emerald-400"> — 对冲抵消了 {fund.paid.toFixed(2)}% 的资金费成本</span>
                    )}
                    <div className="mt-1 opacity-70">对比: 若纯多头持仓 (不hedge), 同期资金费成本 ≈ -{longOnlyCost.toFixed(2)}% (0.03%/天 × 3 仓位)</div>
                  </div>
                );
              })()}
            </div>
          </div>


          {/* 复盘建议: 浏览到复盘阶段时展示 */}
          {viewStage === "review" && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-amber-400" />
                复盘建议（Loop 反馈） <Term k="reviewAdvice" />
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

          </div>
          )}

          {/* 研究详情 折叠面板: 浏览到研究阶段时展示 */}
          {viewStage === "research" && (
          <div className="rounded-xl border bg-card overflow-hidden">
            <button
              onClick={() => setOpenDetails(d => ({ ...d, research: !d.research }))}
              className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold hover:bg-accent/50 transition-colors">
              <span className="flex items-center gap-2"><FlaskConical className="h-4 w-4 text-muted-foreground" />研究详情<span className="text-[11px] font-normal text-muted-foreground">回测对比 · 因子 IC · 因子分层</span></span>
              <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform", openDetails.research && "rotate-180")} />
            </button>
            {openDetails.research && (
              <div className="px-4 pb-4">
          {/* Backtest + IC */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
                回测对比（800天 · 含成本）
                <Term k="btCompare" />
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-muted-foreground border-b">
                      <th className="text-left py-2 pr-3">策略</th>
                      <th className="text-right py-2 px-2"><Term k="annual">年化</Term></th>
                      <th className="text-right py-2 px-2"><Term k="sharpe">夏普</Term></th>
                      <th className="text-right py-2 px-2"><Term k="maxDd">最大回撤</Term></th>
                      <th className="text-right py-2 px-2"><Term k="cum">累计</Term></th>
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
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">因子 IC <Term k="ic" /></h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-muted-foreground border-b">
                      <th className="text-left py-2 pr-3">因子</th>
                      <th className="text-right py-2 px-2"><Term k="ic">IC均值</Term></th>
                      <th className="text-right py-2 px-2"><Term k="ir">IR</Term></th>
                      <th className="text-right py-2 px-2"><Term k="icPos">IC+ 比率</Term></th>
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

          {/* 因子分层: 学术层 / 挖掘层 / 组合层 */}
          <div className="mt-6 rounded-xl border bg-card p-4">
            <div className="flex flex-wrap items-center gap-3 mb-4">
              <h2 className="text-sm font-semibold flex items-center gap-2">
                <FlaskConical className="h-4 w-4 text-amber-400" />
                因子分层 <Term k="factorTiers" />
              </h2>
              <div className="flex rounded-lg border border-border/60 overflow-hidden text-xs font-medium">
                {[
                  { id: "academic", label: `学术层 ${Object.keys(data?.combo.metrics.ic ?? {}).length || ""}` },
                  { id: "mined", label: `挖掘层 ${data?.autopilot_factors?.zoo_count ?? ""}` },
                  { id: "combo", label: `组合层 ${variantCount || ""}` },
                ].map(t => (
                  <button
                    key={t.id}
                    onClick={() => setTier(t.id as "academic" | "mined" | "combo")}
                    className={cn(
                      "px-3 py-1.5 transition",
                      tier === t.id ? "bg-amber-500/15 text-amber-300" : "text-muted-foreground hover:bg-accent",
                    )}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              <span className="text-xs text-muted-foreground ml-auto">
                学术(文献) → 挖掘(factor miner) → 组合(变体) · 下层供上层
              </span>
            </div>

            {/* 学术层: 文献因子 IC + 完整因子池 */}
            {tier === "academic" && (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-xs text-muted-foreground border-b">
                        <th className="text-left py-2 pr-3">因子</th>
                        <th className="text-right py-2 px-2"><Term k="ic">IC均值</Term></th>
                        <th className="text-right py-2 px-2"><Term k="ir">IR</Term></th>
                        <th className="text-right py-2 px-2"><Term k="icPos">IC+ 比率</Term></th>
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
                <div className="mt-2 text-xs text-muted-foreground">
                  已评估 3 因子（回测期 800 天）· 组合基座：BAB + high52w · RMW 备选（COMBO3 跑输）
                </div>
                <div className="text-xs text-muted-foreground mt-4 mb-2">完整学术因子池（{ACADEMIC_FACTORS.length} 个 · 文献因子）</div>
                <div className="flex flex-wrap gap-2">
                  {ACADEMIC_FACTORS.map(f => {
                    const inCombo = f.id === "BAB" || f.id === "high52w";
                    const evaluated = f.id === "BAB" || f.id === "high52w" || f.id === "RMW";
                    const inPool = f.id === "RMW";
                    return (
                      <span
                        key={f.id}
                        className={cn(
                          "rounded-full border px-2.5 py-1 text-[11px] font-mono",
                          inCombo
                            ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-300"
                            : evaluated
                              ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-400"
                              : inPool
                                ? "border-amber-500/30 bg-amber-500/5 text-amber-400"
                                : "border-border/60 bg-muted/30 text-muted-foreground",
                        )}
                        title={f.desc}
                      >
                        {f.id}
                        <span className="ml-1 font-sans text-[10px] opacity-70">{f.name}</span>
                        {inCombo && <span className="ml-1 text-[10px]">· 组合基座</span>}
                        {evaluated && !inCombo && <span className="ml-1 text-[10px]">· 已评估</span>}
                        {!evaluated && <span className="ml-1 text-[10px]">· 待评估</span>}
                      </span>
                    );
                  })}
                </div>
                <div className="mt-3 text-xs text-muted-foreground">
                  待评估学术因子已加入变体生成候选 → 每日 08:45 自动回测 → 跑赢基策略晋升 testing
                </div>
              </>
            )}

            {/* 挖掘层: factor miner 产出 */}
            {tier === "mined" && (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                  {[
                    { label: "zoo 因子总数", value: `${data?.autopilot_factors?.zoo_count ?? "--"}` },
                    { label: "活跃（交易中）", value: `${data?.autopilot_factors?.active?.length ?? "--"}`, color: "text-emerald-400" },
                    { label: <Term k="exploring">候选（未审判）</Term>, value: `${minedCandidates.length}`, color: "text-amber-400" },
                    { label: "退役（唯一因子）", value: `${retiredUniqueCount}`, color: "text-muted-foreground" },
                  ].map((k, ki) => (
                    <div key={ki} className="rounded-lg border border-border/60 p-3">
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
                          <th className="text-left py-2 px-2"><Term k="lifecycle">生命周期</Term></th>
                          <th className="text-right py-2 px-2"><Term k="tradeCount">交易数</Term></th>
                          <th className="text-right py-2 px-2"><Term k="winRate">胜率</Term></th>
                          <th className="text-right py-2 px-2"><Term k="pf">Profit Factor</Term></th>
                          <th className="text-right py-2 px-2"><Term k="sharpe">Sharpe</Term></th>
                          <th className="text-right py-2 px-2"><Term k="ic">IC</Term></th>
                          <th className="text-right py-2 px-2"><Term k="pnl">已实现 PnL</Term></th>
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
                    {data?.autopilot_factors ? "暂无活跃挖掘因子（zoo 因子待过三关准入）" : "挖掘数据不可用（Autopilot 未启动）"}
                  </div>
                )}
                {minedCandidates.length ? (
                  <>
                    <div className="text-xs text-muted-foreground mt-4 mb-2">zoo 候选（未激活未退役）</div>
                    <div className="flex flex-wrap gap-2">
                      {minedCandidates.slice(0, 24).map(z => (
                        <span key={z.alpha_id} className="rounded-full border border-border/60 bg-muted/30 px-2.5 py-1 text-[11px] font-mono text-muted-foreground">
                          {z.alpha_id.replace("crypto_mined_", "")}
                          {z.nickname && <span className="ml-1 text-muted-foreground/60">{z.nickname}</span>}
                        </span>
                      ))}
                      {minedCandidates.length > 24 && (
                        <span className="text-[11px] text-muted-foreground">+{minedCandidates.length - 24} 个…</span>
                      )}
                    </div>
                  </>
                ) : null}
                {data?.autopilot_factors?.retired?.length ? (
                  <>
                    <div className="text-xs text-muted-foreground mt-4 mb-2">
                      已退役因子（{data?.autopilot_factors?.retired?.length ?? 0} 条记录 · {retiredUniqueCount} 个唯一因子）
                    </div>
                    <div className="overflow-x-auto max-h-[240px] overflow-y-auto">
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
                            <tr key={`${f.alpha_id}-${f.retired_at}`} className="border-b border-muted/50">
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
              </>
            )}

            {/* 组合层: 变体候选 */}
            {tier === "combo" && (
              <div className="grid grid-cols-1 gap-2">
                {hypotheses.filter(h => h.status === "exploring" || h.status === "testing").map(h => {
                  const m = review?.variant_metrics?.[h.hypothesis_id];
                  return (
                    <div key={h.hypothesis_id} className="flex flex-wrap items-center gap-2 rounded-lg border border-border/50 px-3 py-2 text-xs">
                      <span className={cn("shrink-0 rounded-full px-2 py-0.5 font-semibold", h.status === "testing" ? "bg-amber-500/20 text-amber-400" : "bg-muted text-muted-foreground")}>
                        {STATUS_LABEL[h.status] ?? h.status}
                      </span>
                      <span className="font-medium">{h.title.replace("BAB+high52w 双因子组合 · ", "")}</span>
                      {m && (
                        <>
                          <span className={cn("font-mono", (m.annual ?? 0) > 0 ? "text-emerald-400" : "text-rose-400")}>
                            <Term k="annual">年化</Term> {m.annual != null ? `${m.annual > 0 ? "+" : ""}${m.annual}%` : "--"}
                          </span>
                          <span className="font-mono text-muted-foreground"><Term k="sharpe">夏普</Term> {m.sharpe ?? "--"}</span>
                          <span className="font-mono text-rose-400"><Term k="maxDd">回撤</Term> {m.max_dd ?? "--"}%</span>
                        </>
                      )}
                      {h.status === "testing" && <span className="ml-auto text-amber-300">✅ 已晋升，可进模拟</span>}
                      {h.signal_definition && !seededDefs.has(h.signal_definition) && (
                        <button
                          onClick={() => seedVariant(h)}
                          disabled={mutating}
                          className="ml-auto rounded-md border border-cyan-500/40 bg-cyan-500/10 px-2.5 py-1 text-[11px] font-semibold text-cyan-300 hover:bg-cyan-500/20 disabled:opacity-50"
                        >
                          + 播种为并行策略
                        </button>
                      )}
                      {h.signal_definition && seededDefs.has(h.signal_definition) && (
                        <span className="ml-auto text-[11px] text-muted-foreground">已在运行</span>
                      )}
                    </div>
                  );
                })}
                {!variantCount && <div className="text-xs text-muted-foreground">暂无变体候选 — 基策略 validated 后自动生成</div>}
                <div className="mt-2 text-xs text-muted-foreground">
                  变体来源：学术层 × 挖掘层 组合 · 每日 08:45 自动回测 · 跑赢基策略自动晋升 testing
                </div>
              </div>
            )}
          </div>

              </div>
            )}
          </div>
          )}

{/* 执行详情 折叠面板: 浏览到执行阶段时展示 (与研究详情同构) */}
          {viewStage === "live" && (
          <div className="rounded-xl border bg-card overflow-hidden">
            <button
              onClick={() => setOpenDetails(d => ({ ...d, exec: !d.exec }))}
              className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold hover:bg-accent/50 transition-colors">
              <span className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-muted-foreground" />执行详情<span className="text-[11px] font-normal text-muted-foreground">执行状态 · 执行明细</span></span>
              <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform", openDetails.exec && "rotate-180")} />
            </button>
            {openDetails.exec && (
              <div className="px-4 pb-4">
          {/* 执行层状态 + 流水线生命周期记录 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="rounded-xl border bg-card p-4">
              <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-emerald-400" />
                执行层状态（Autopilot） <Term k="autopilotStatus" />
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
                    <div className="font-mono text-lg font-bold mt-0.5">{PIPELINE_PHASE_LABEL[autopilot.pipeline.phase] ?? autopilot.pipeline.phase}</div>
                  </div>
                  <div className="rounded-lg border border-border/60 p-3">
                    <div className="text-[11px] text-muted-foreground">今日订单</div>
                    <div className="font-mono text-lg font-bold mt-0.5">{autopilot.counter.count} <span className="text-xs text-muted-foreground">/ {autopilot.config.max_trades_per_day}</span></div>
                  </div>
                  <div className="rounded-lg border border-border/60 p-3">
                    <div className="text-[11px] text-muted-foreground">监控币对 <span className="text-[10px]">({autopilot.config.pairs.length})</span></div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {autopilot.config.pairs.map(p => (
                        <span key={p} className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">{p}</span>
                      ))}
                    </div>
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

          {/* 执行明细: 表现 + 持仓 + 交易记录 */}
          <div className="rounded-xl border bg-card p-4">
            <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
              <Receipt className="h-4 w-4 text-cyan-400" />
              执行明细（纸面账户） <Term k="execDetail" />
            </h2>
            {data?.autopilot_performance ? (
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
                {[
                  { label: <Term k="winRate">胜率</Term>, value: `${(data.autopilot_performance.win_rate * 100).toFixed(1)}%`, color: "text-emerald-400" },
                  { label: <Term k="pnl">已实现 PnL</Term>, value: `$${data.autopilot_performance.realized_pnl_usd.toLocaleString()}`, color: data.autopilot_performance.realized_pnl_usd >= 0 ? "text-emerald-400" : "text-rose-400" },
                  { label: <Term k="tradeCount">总交易</Term>, value: `${data.autopilot_performance.total_trades}` },
                  { label: <Term k="sharpe">夏普</Term>, value: data.autopilot_performance.sharpe.toFixed(2) },
                  { label: <Term k="maxDd">最大回撤</Term>, value: `${(data.autopilot_performance.max_drawdown * 100).toFixed(1)}%`, color: "text-rose-400" },
                ].map((k, ki) => (
                  <div key={ki} className="rounded-lg border border-border/60 p-3">
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
          </div>

              </div>
            )}
          </div>
          )}

          {/* 候选组合 折叠面板: 组合阶段的内容 (变体池状态档案) — 浏览到组合阶段时展示 */}
          {viewStage === "compose" && (
          <div className="rounded-xl border bg-card overflow-hidden">
            <button
              onClick={() => setOpenDetails(d => ({ ...d, registry: !d.registry }))}
              className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold hover:bg-accent/50 transition-colors">
              <span className="flex items-center gap-2"><Layers className="h-4 w-4 text-muted-foreground" />候选组合<span className="text-[11px] font-normal text-muted-foreground">因子假设 + 变体组合档案</span></span>
              <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform", openDetails.registry && "rotate-180")} />
            </button>
            {openDetails.registry && (
              <div className="px-4 pb-4">
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
                          {STATUS_LABEL[h.status] ?? h.status}
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
            )}
          </div>
          )}

        </>
      )}
    </div>
  );
}
