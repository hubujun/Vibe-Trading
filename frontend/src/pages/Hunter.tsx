import { useEffect, useState } from "react";
import {
  CheckCircle2,
  CircleDashed,
  Crosshair,
  FlaskConical,
  Flame,
  Pencil,
  Plus,
  RefreshCw,
  Target,
  Trash2,
} from "lucide-react";
import { api, HunterOpportunity, HunterPaperResponse, HunterResponse, HunterShot } from "../lib/api";
import { cn } from "../lib/utils";

/** 术语提示: ? 圆圈 + hover 白话解释 (与工作台 Term 同款, 独立页面内嵌) */
const TERMS: Record<string, string> = {
  liqdist:
    "百倍爆仓距离：100x 下单后反向约 0.5~1% 就爆仓。真正赌的不是方向，是“进场后价格不再回撤 1%”的路径",
  path:
    "路径风险：方向看对但中间插一针 1% 就出局。只有上新揭晓瞬间 / 轧空点火 / 破位瀑布这类单向惯性大的时刻才配得上百倍",
  lottery:
    "彩票仓：单注面值 = 连输 10 次不心疼的钱（如固定 100~200U）。百倍的正确用法是控制彩票面值，不是放大仓位",
  trigger:
    "触发器：等待期不盯盘。费率 / OI / 价格 / 公告条件提前写死，命中才动手——决策在冷静期做，触发时只执行不思考",
  pathclean:
    "路径干净度 = 进场后多大可能一路不回撤。五类机会按路径干净度排序：上新首日 > 轧空点火 > 清算瀑布 > 赶顶反转 > 脱锚修复",
};
function Term({ k }: { k: keyof typeof TERMS | string }) {
  return (
    <span
      title={TERMS[k as string] ?? k}
      className="ml-0.5 inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full bg-muted text-[9px] text-muted-foreground align-middle"
    >
      ?
    </span>
  );
}

const KIND_LABEL: Record<string, { label: string; cls: string }> = {
  listing: { label: "上新/首日", cls: "bg-violet-500/10 text-violet-500" },
  squeeze: { label: "轧空点火", cls: "bg-orange-500/10 text-orange-500" },
  liquidation: { label: "清算瀑布", cls: "bg-rose-500/10 text-rose-500" },
  blowoff: { label: "赶顶反转", cls: "bg-sky-500/10 text-sky-500" },
  depeg: { label: "脱锚修复", cls: "bg-emerald-500/10 text-emerald-500" },
  other: { label: "其他", cls: "bg-muted text-muted-foreground" },
};

const STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  watching: { label: "观察中", cls: "bg-sky-500/10 text-sky-500" },
  triggered: { label: "已触发", cls: "bg-amber-500/10 text-amber-500" },
  won: { label: "赢了", cls: "bg-emerald-500/10 text-emerald-500" },
  lost: { label: "爆了", cls: "bg-rose-500/10 text-rose-500" },
  discarded: { label: "放弃", cls: "bg-muted text-muted-foreground" },
};

const OUTCOME_LABEL: Record<string, { label: string; cls: string }> = {
  open: { label: "持仓中", cls: "bg-amber-500/10 text-amber-500" },
  won: { label: "赢", cls: "bg-emerald-500/10 text-emerald-500" },
  lost: { label: "亏", cls: "bg-rose-500/10 text-rose-500" },
};

const inputCls =
  "w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus:border-primary";

function Badge({ label, cls }: { label: string; cls: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
        cls
      )}
    >
      {label}
    </span>
  );
}

interface OppFormState {
  inst: string;
  kind: string;
  direction: "long" | "short";
  catalyst: string;
  trigger: string;
  plan: string;
  note: string;
}

interface ShotFormState {
  at: string;
  inst: string;
  direction: "long" | "short";
  leverage: string;
  margin: string;
  entry: string;
  exit: string;
  pnl: string;
  outcome: "open" | "won" | "lost";
  note: string;
}

const EMPTY_OPP: OppFormState = {
  inst: "",
  kind: "listing",
  direction: "long" as const,
  catalyst: "",
  trigger: "",
  plan: "",
  note: "",
};

export function Hunter() {
  const [data, setData] = useState<HunterResponse | null>(null);
  const [paper, setPaper] = useState<HunterPaperResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 机会表单: null = 收起; "" = 新增; id = 编辑该条
  const [oppFormOpen, setOppFormOpen] = useState<string | null>(null);
  const [oppForm, setOppForm] = useState<OppFormState>({ ...EMPTY_OPP });
  const [shotFormOpen, setShotFormOpen] = useState(false);
  const [shotForm, setShotForm] = useState<ShotFormState>({
    at: "",
    inst: "",
    direction: "long" as const,
    leverage: "100",
    margin: "",
    entry: "",
    exit: "",
    pnl: "",
    outcome: "won" as const,
    note: "",
  });
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setError(null);
    try {
      const d = await api.getHunter();
      setData(d);
    } catch (e) {
      setError(`加载失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  // 玩法体检数据派生 (100x 档用于结论文案)
  const listingPaper = paper?.listing ?? null;
  const lv100 = listingPaper?.levs.find((l) => l.lev === 100) ?? null;
  const slRate100 =
    lv100 && lv100.n > 0 ? Math.round((lv100.sl / lv100.n) * 100) : null;
  const ev100 =
    lv100 != null ? `${lv100.ev >= 0 ? "+" : ""}${lv100.ev}U/注` : "--";

  useEffect(() => {
    load();
    api
      .getHunterPaper()
      .then((p) => setPaper(p))
      .catch(() => setPaper(null));
  }, []);

  const opps = data?.opportunities ?? [];
  const shots = data?.shots ?? [];
  const watching = opps.filter((o) => o.status === "watching").length;
  const triggered = opps.filter((o) => o.status === "triggered").length;
  const closed = shots.filter((s) => s.outcome !== "open");
  const pnl = closed.reduce((acc, s) => acc + (s.pnl ?? 0), 0);
  const wins = closed.filter((s) => s.outcome === "won").length;

  // ---- 机会操作 ----
  const openOppForm = (o?: HunterOpportunity) => {
    if (!o) {
      setOppForm({ ...EMPTY_OPP });
      setOppFormOpen("");
    } else {
      setOppForm({
        inst: o.inst,
        kind: o.kind,
        direction: o.direction,
        catalyst: o.catalyst ?? "",
        trigger: o.trigger ?? "",
        plan: o.plan ?? "",
        note: o.note ?? "",
      });
      setOppFormOpen(o.id);
    }
  };

  const submitOpp = async () => {
    if (!oppForm.inst.trim()) return;
    setBusy(true);
    setError(null);
    try {
      if (oppFormOpen) {
        await api.patchHunterOpportunity(oppFormOpen, { ...oppForm });
      } else {
        await api.createHunterOpportunity({ ...oppForm, status: "watching" });
      }
      setOppFormOpen(null);
      await load();
    } catch (e) {
      setError(`保存失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const setOppStatus = async (o: HunterOpportunity, status: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.patchHunterOpportunity(o.id, { status });
      await load();
    } catch (e) {
      setError(`更新失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const deleteOpp = async (o: HunterOpportunity) => {
    if (!window.confirm(`删除机会 ${o.inst}？`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteHunterOpportunity(o.id);
      await load();
    } catch (e) {
      setError(`删除失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  // ---- 开仓记录操作 ----
  const num = (v: string): number | null => {
    if (v === "" || v == null) return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };

  const submitShot = async () => {
    if (!shotForm.inst.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.createHunterShot({
        at: shotForm.at.replace("T", " ").trim(),
        inst: shotForm.inst.trim(),
        direction: shotForm.direction,
        leverage: num(shotForm.leverage) ?? 100,
        margin: num(shotForm.margin) ?? 0,
        entry: num(shotForm.entry),
        exit: num(shotForm.exit),
        pnl: num(shotForm.pnl),
        outcome: shotForm.outcome,
        note: shotForm.note.trim(),
      });
      setShotFormOpen(false);
      setShotForm({
        at: "",
        inst: "",
        direction: "long",
        leverage: "100",
        margin: "",
        entry: "",
        exit: "",
        pnl: "",
        outcome: "won",
        note: "",
      });
      await load();
    } catch (e) {
      setError(`保存失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const deleteShot = async (s: HunterShot) => {
    if (!window.confirm(`删除这笔记录 (${s.at} ${s.inst})？`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteHunterShot(s.id);
      await load();
    } catch (e) {
      setError(`删除失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  // ---- 状态流转按钮 ----
  const statusActions = (o: HunterOpportunity) => {
    const btn = (status: string, label: string, cls: string) => (
      <button
        key={status}
        onClick={() => setOppStatus(o, status)}
        disabled={busy}
        className={cn(
          "rounded-md px-2 py-1 text-[11px] font-medium transition-colors disabled:opacity-40",
          cls
        )}
      >
        {label}
      </button>
    );
    if (o.status === "watching")
      return (
        <>
          {btn("triggered", "🎯 已触发", "bg-amber-500/10 text-amber-500 hover:bg-amber-500/20")}
          {btn("discarded", "放弃", "bg-muted text-muted-foreground hover:bg-muted/70")}
        </>
      );
    if (o.status === "triggered")
      return (
        <>
          {btn("won", "赢了", "bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20")}
          {btn("lost", "爆了", "bg-rose-500/10 text-rose-500 hover:bg-rose-500/20")}
          {btn("watching", "回观察", "bg-muted text-muted-foreground hover:bg-muted/70")}
        </>
      );
    return btn("watching", "重新观察", "bg-muted text-muted-foreground hover:bg-muted/70");
  };

  const statCards = [
    {
      label: "观察中",
      value: String(watching),
      cls: "text-sky-500",
      icon: <CircleDashed className="h-4 w-4" />,
    },
    {
      label: "已触发",
      value: String(triggered),
      cls: "text-amber-500",
      icon: <Target className="h-4 w-4" />,
    },
    {
      label: "总开仓",
      value: String(shots.length),
      cls: "text-foreground",
      icon: <Crosshair className="h-4 w-4" />,
    },
    {
      label: `胜率 ${closed.length ? ((wins / closed.length) * 100).toFixed(0) : "--"}%`,
      value: `${wins}赢/${closed.length - wins}亏`,
      cls: "text-foreground",
      icon: <CheckCircle2 className="h-4 w-4" />,
    },
    {
      label: "累计盈亏",
      value: `${pnl >= 0 ? "+" : ""}${pnl.toFixed(1)} U`,
      cls: pnl >= 0 ? "text-emerald-500" : "text-rose-500",
      icon: <Flame className="h-4 w-4" />,
    },
  ];

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <Crosshair className="h-6 w-6 text-rose-500" /> 事件猎手
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            百倍杠杆机会跟踪台 — 长期等待、概率够大才出手。候选机会 + 开仓账本
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-muted/60 disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          刷新
        </button>
      </div>

      <div className="rounded-xl border bg-card p-4 text-xs text-muted-foreground space-y-1.5">
        <p>
          <span className="font-medium text-foreground">玩法怎么用：</span>
          ① 平时把够格的机会录进候选清单，等 <Term k="trigger" /> 命中；② 出手前先看
          <Term k="liqdist" /> 和 <Term k="path" /> —— 路径不干净的不配百倍；③ 单注永远走{" "}
          <Term k="lottery" /> 面值，赚了是 bonus，亏光是学费。
        </p>
        <p className="text-rose-500">
          五类机会（按路径干净度排序）：上新/首日 · 轧空点火 · 清算瀑布 · 赶顶反转 · 脱锚修复{" "}
          <Term k="pathclean" />
          —— 与 Vibe 1500U 实盘严格隔离，这里只放输得起的彩票钱。
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-500">
          {error}
        </div>
      )}

      {/* 统计条 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {statCards.map((s) => (
          <div key={s.label} className="rounded-xl border bg-card p-3">
            <div className={cn("flex items-center gap-1.5 text-xs text-muted-foreground", s.cls)}>
              {s.icon}
              {s.label}
            </div>
            <div className="mt-1 font-mono text-xl font-semibold">{s.value}</div>
          </div>
        ))}
      </div>

      {loading ? (
        <div className="flex h-[30vh] items-center justify-center text-muted-foreground">
          <RefreshCw className="mr-2 h-5 w-5 animate-spin" /> 加载中…
        </div>
      ) : (
        <>
          {/* ============ 候选机会 ============ */}
          <section className="rounded-xl border bg-card">
            <div className="flex items-center justify-between border-b px-4 py-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                <Target className="h-4 w-4 text-amber-500" />
                候选机会
                <span className="text-xs font-normal text-muted-foreground">
                  {opps.length} 条 · watching 等待触发器，triggered 等结算
                </span>
              </h2>
              <button
                onClick={() => openOppForm()}
                disabled={busy}
                className="flex items-center gap-1 rounded-md border bg-background px-2.5 py-1.5 text-xs transition-colors hover:bg-muted/60 disabled:opacity-50"
              >
                <Plus className="h-3.5 w-3.5" /> 新增机会
              </button>
            </div>

            {/* 新增/编辑表单 */}
            {oppFormOpen !== null && (
              <div className="space-y-3 border-b bg-muted/20 px-4 py-3">
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <label className="text-xs">
                    <span className="text-muted-foreground">标的 *</span>
                    <input
                      className={inputCls}
                      value={oppForm.inst}
                      onChange={(e) => setOppForm({ ...oppForm, inst: e.target.value })}
                      placeholder="TRUMP-USDT-SWAP / BTC"
                    />
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">机会类型</span>
                    <select
                      className={inputCls}
                      value={oppForm.kind}
                      onChange={(e) => setOppForm({ ...oppForm, kind: e.target.value })}
                    >
                      {Object.entries(KIND_LABEL).map(([k, v]) => (
                        <option key={k} value={k}>
                          {v.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">预期方向</span>
                    <select
                      className={inputCls}
                      value={oppForm.direction}
                      onChange={(e) =>
                        setOppForm({ ...oppForm, direction: e.target.value as "long" | "short" })
                      }
                    >
                      <option value="long">做多</option>
                      <option value="short">做空</option>
                    </select>
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">状态</span>
                    <div className="rounded-md border bg-background px-2.5 py-1.5 text-sm">
                      {oppFormOpen ? "编辑中（保留原状态）" : "新增 → 观察中"}
                    </div>
                  </label>
                </div>
                <label className="block text-xs">
                  <span className="text-muted-foreground">催化剂（为什么它可能大动）</span>
                  <input
                    className={inputCls}
                    value={oppForm.catalyst}
                    onChange={(e) => setOppForm({ ...oppForm, catalyst: e.target.value })}
                    placeholder="如：OKX 公告即将上线 XX 永续，情绪币 + 上新首日"
                  />
                </label>
                <label className="block text-xs">
                  <span className="text-muted-foreground">
                    触发条件（命中才动手，写死数字） <Term k="trigger" />
                  </span>
                  <input
                    className={inputCls}
                    value={oppForm.trigger}
                    onChange={(e) => setOppForm({ ...oppForm, trigger: e.target.value })}
                    placeholder="如：上线首日 5m 放量阳线确认 / 8h 费率 ≤ -0.05% 且 OI 新高"
                  />
                </label>
                <label className="block text-xs">
                  <span className="text-muted-foreground">剧本（进场 / 止盈 / 失效条件）</span>
                  <input
                    className={inputCls}
                    value={oppForm.plan}
                    onChange={(e) => setOppForm({ ...oppForm, plan: e.target.value })}
                    placeholder="如：确认后进 100U 面值 → +100% 先出半 → 峰值回撤 30% 清 → 30 分钟不动就走"
                  />
                </label>
                <label className="block text-xs">
                  <span className="text-muted-foreground">备注</span>
                  <input
                    className={inputCls}
                    value={oppForm.note}
                    onChange={(e) => setOppForm({ ...oppForm, note: e.target.value })}
                    placeholder="可选：参考依据、链接、上一轮教训"
                  />
                </label>
                <div className="flex gap-2">
                  <button
                    onClick={submitOpp}
                    disabled={busy || !oppForm.inst.trim()}
                    className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
                  >
                    {oppFormOpen ? "保存修改" : "加入观察清单"}
                  </button>
                  <button
                    onClick={() => setOppFormOpen(null)}
                    disabled={busy}
                    className="rounded-md border px-3 py-1.5 text-xs transition-colors hover:bg-muted/60 disabled:opacity-40"
                  >
                    取消
                  </button>
                </div>
              </div>
            )}

            {/* 机会列表 */}
            <div className="space-y-2 p-3">
              {opps.length === 0 && (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  还没有候选机会。够格的局才值得等——先想清楚催化剂和触发条件，再录进来。
                </p>
              )}
              {opps.map((o) => {
                const kind = KIND_LABEL[o.kind] ?? KIND_LABEL.other;
                const st = STATUS_LABEL[o.status] ?? STATUS_LABEL.watching;
                return (
                  <div key={o.id} className="rounded-lg border bg-background p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-[13px] font-semibold">{o.inst}</span>
                        <Badge label={kind.label} cls={kind.cls} />
                        <Badge
                          label={o.direction === "long" ? "做多" : "做空"}
                          cls={
                            o.direction === "long"
                              ? "bg-rose-500/10 text-rose-500"
                              : "bg-emerald-500/10 text-emerald-500"
                          }
                        />
                        <Badge label={st.label} cls={st.cls} />
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {statusActions(o)}
                        <button
                          onClick={() => openOppForm(o)}
                          disabled={busy}
                          title="编辑"
                          className="rounded-md border p-1.5 text-muted-foreground transition-colors hover:bg-muted/60 disabled:opacity-40"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => deleteOpp(o)}
                          disabled={busy}
                          title="删除"
                          className="rounded-md border p-1.5 text-muted-foreground transition-colors hover:bg-rose-500/10 hover:text-rose-500 disabled:opacity-40"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                    <div className="mt-2 space-y-1 text-xs">
                      {o.catalyst && (
                        <p className="text-muted-foreground">
                          <span className="text-foreground/70">催化剂：</span>
                          {o.catalyst}
                        </p>
                      )}
                      {o.trigger && (
                        <p className="text-muted-foreground">
                          <span className="text-foreground/70">触发：</span>
                          {o.trigger}
                        </p>
                      )}
                      {o.plan && (
                        <p className="text-muted-foreground">
                          <span className="text-foreground/70">剧本：</span>
                          {o.plan}
                        </p>
                      )}
                      {o.note && <p className="text-muted-foreground/70">备注：{o.note}</p>}
                      <p className="text-[10px] text-muted-foreground/50">
                        录入 {o.created_at}
                        {o.updated_at !== o.created_at && ` · 更新 ${o.updated_at}`}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          {/* ============ 开仓账本 ============ */}
          <section className="rounded-xl border bg-card">
            <div className="flex items-center justify-between border-b px-4 py-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                <Flame className="h-4 w-4 text-rose-500" />
                开仓账本
                <span className="text-xs font-normal text-muted-foreground">
                  {shots.length} 注 · 彩票仓面值，攒真实样本
                  <Term k="lottery" />
                </span>
              </h2>
              <button
                onClick={() => setShotFormOpen((v) => !v)}
                disabled={busy}
                className="flex items-center gap-1 rounded-md border bg-background px-2.5 py-1.5 text-xs transition-colors hover:bg-muted/60 disabled:opacity-50"
              >
                <Plus className="h-3.5 w-3.5" /> {shotFormOpen ? "收起" : "记一笔"}
              </button>
            </div>

            {shotFormOpen && (
              <div className="space-y-3 border-b bg-muted/20 px-4 py-3">
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <label className="text-xs">
                    <span className="text-muted-foreground">时间（空 = 现在）</span>
                    <input
                      className={inputCls}
                      type="datetime-local"
                      value={shotForm.at}
                      onChange={(e) => setShotForm({ ...shotForm, at: e.target.value })}
                    />
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">标的 *</span>
                    <input
                      className={inputCls}
                      value={shotForm.inst}
                      onChange={(e) => setShotForm({ ...shotForm, inst: e.target.value })}
                      placeholder="TRUMP-USDT-SWAP"
                    />
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">方向</span>
                    <select
                      className={inputCls}
                      value={shotForm.direction}
                      onChange={(e) =>
                        setShotForm({ ...shotForm, direction: e.target.value as "long" | "short" })
                      }
                    >
                      <option value="long">做多</option>
                      <option value="short">做空</option>
                    </select>
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">杠杆</span>
                    <input
                      className={inputCls}
                      type="number"
                      min={1}
                      max={200}
                      value={shotForm.leverage}
                      onChange={(e) => setShotForm({ ...shotForm, leverage: e.target.value })}
                    />
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">保证金面值 (U) *</span>
                    <input
                      className={inputCls}
                      type="number"
                      step="any"
                      value={shotForm.margin}
                      onChange={(e) => setShotForm({ ...shotForm, margin: e.target.value })}
                      placeholder="100"
                    />
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">入场价</span>
                    <input
                      className={inputCls}
                      type="number"
                      step="any"
                      value={shotForm.entry}
                      onChange={(e) => setShotForm({ ...shotForm, entry: e.target.value })}
                    />
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">出场价</span>
                    <input
                      className={inputCls}
                      type="number"
                      step="any"
                      value={shotForm.exit}
                      onChange={(e) => setShotForm({ ...shotForm, exit: e.target.value })}
                    />
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">盈亏 (U, 带符号)</span>
                    <input
                      className={inputCls}
                      type="number"
                      step="any"
                      value={shotForm.pnl}
                      onChange={(e) => setShotForm({ ...shotForm, pnl: e.target.value })}
                      placeholder="+150 / -100"
                    />
                  </label>
                  <label className="text-xs">
                    <span className="text-muted-foreground">结果</span>
                    <select
                      className={inputCls}
                      value={shotForm.outcome}
                      onChange={(e) =>
                        setShotForm({ ...shotForm, outcome: e.target.value as "open" | "won" | "lost" })
                      }
                    >
                      <option value="won">赢了</option>
                      <option value="lost">亏了</option>
                      <option value="open">持仓中</option>
                    </select>
                  </label>
                  <label className="col-span-2 text-xs">
                    <span className="text-muted-foreground">备注（对应哪个机会 / 哪类玩法）</span>
                    <input
                      className={inputCls}
                      value={shotForm.note}
                      onChange={(e) => setShotForm({ ...shotForm, note: e.target.value })}
                      placeholder="如：TRUMP 上新首日确认后进场，+120% 出半，回撤清"
                    />
                  </label>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={submitShot}
                    disabled={busy || !shotForm.inst.trim()}
                    className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
                  >
                    记入账本
                  </button>
                  <button
                    onClick={() => setShotFormOpen(false)}
                    disabled={busy}
                    className="rounded-md border px-3 py-1.5 text-xs transition-colors hover:bg-muted/60 disabled:opacity-40"
                  >
                    取消
                  </button>
                </div>
              </div>
            )}

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                    <th className="px-3 py-2.5 font-medium">时间</th>
                    <th className="px-3 py-2.5 font-medium">标的</th>
                    <th className="px-3 py-2.5 font-medium">方向</th>
                    <th className="px-3 py-2.5 font-medium text-right">杠杆</th>
                    <th className="px-3 py-2.5 font-medium text-right">面值 (U)</th>
                    <th className="px-3 py-2.5 font-medium text-right">入场</th>
                    <th className="px-3 py-2.5 font-medium text-right">出场</th>
                    <th className="px-3 py-2.5 font-medium text-right">盈亏 (U)</th>
                    <th className="px-3 py-2.5 font-medium">结果</th>
                    <th className="px-3 py-2.5 font-medium">备注</th>
                    <th className="px-2 py-2.5" />
                  </tr>
                </thead>
                <tbody>
                  {shots.length === 0 && (
                    <tr>
                      <td colSpan={11} className="px-3 py-6 text-center text-sm text-muted-foreground">
                        还没有开仓记录。真出手了才记——账本是用来回头算这个玩法到底赚不赚钱的。
                      </td>
                    </tr>
                  )}
                  {shots.map((s) => {
                    const oc = OUTCOME_LABEL[s.outcome] ?? OUTCOME_LABEL.open;
                    return (
                      <tr key={s.id} className="border-b border-border/40 last:border-0 hover:bg-muted/30">
                        <td className="px-3 py-2 font-mono text-xs text-muted-foreground whitespace-nowrap">
                          {s.at}
                        </td>
                        <td className="px-3 py-2 font-mono text-[13px] font-medium">{s.inst}</td>
                        <td className="px-3 py-2">
                          <span
                            className={cn(
                              "font-medium",
                              s.direction === "long" ? "text-rose-500" : "text-emerald-500"
                            )}
                          >
                            {s.direction === "long" ? "多" : "空"}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-[13px]">
                          {s.leverage}x
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-[13px]">{s.margin}</td>
                        <td className="px-3 py-2 text-right font-mono text-xs">
                          {s.entry ?? "—"}
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-xs">
                          {s.exit ?? "—"}
                        </td>
                        <td
                          className={cn(
                            "px-3 py-2 text-right font-mono text-[13px]",
                            s.pnl == null
                              ? "text-muted-foreground"
                              : s.pnl >= 0
                                ? "text-emerald-500"
                                : "text-rose-500"
                          )}
                        >
                          {s.pnl == null ? "—" : `${s.pnl >= 0 ? "+" : ""}${s.pnl.toFixed(1)}`}
                        </td>
                        <td className="px-3 py-2">
                          <Badge label={oc.label} cls={oc.cls} />
                        </td>
                        <td className="max-w-[200px] truncate px-3 py-2 text-xs text-muted-foreground">
                          {s.note || "—"}
                        </td>
                        <td className="px-2 py-2">
                          <button
                            onClick={() => deleteShot(s)}
                            disabled={busy}
                            title="删除记录"
                            className="rounded-md border p-1.5 text-muted-foreground transition-colors hover:bg-rose-500/10 hover:text-rose-500 disabled:opacity-40"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          {/* ============ 玩法体检 ============ */}
          <section className="rounded-xl border bg-card">
            <div className="flex items-center justify-between border-b px-4 py-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                <FlaskConical className="h-4 w-4 text-cyan-500" />
                玩法体检
                <span className="text-xs font-normal text-muted-foreground">
                  历史数据压力测试这个玩法 · 结论比信号重要
                </span>
              </h2>
              {(() => {
                const t = paper?.listing?.generated_at ?? paper?.squeeze?.generated_at;
                return t ? (
                  <span className="text-[11px] text-muted-foreground">生成于 {t}</span>
                ) : null;
              })()}
            </div>

            {!paper?.listing && !paper?.squeeze && !paper?.resonance ? (
              <p className="px-4 py-6 text-center text-sm text-muted-foreground">
                体检数据未生成 — 跑 <span className="font-mono">python3 ~/.hermes/scripts/hunter_paper.py</span>{" "}
                与 <span className="font-mono">hunter_paper_squeeze.py</span> 后刷新
              </p>
            ) : (
              <div className="space-y-3 p-3">
                {/* ---- listing 上新首日 ---- */}
                {paper?.listing && (
                  <div className="rounded-lg border bg-background p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-[13px] font-semibold">上新首日 · 100x 彩票仓杠杆对照</h3>
                      <span className="rounded-full bg-cyan-500/10 px-2 py-0.5 text-[11px] text-cyan-500">
                        {paper.listing.lookback_days} 天 · {paper.listing.samples} 新币 ·{" "}
                        {paper.listing.signal_coins} 信号
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      首日第一根 |5m实体|≥3% 脉冲 = 揭晓确认 → 同向进 · 面值 100U · 每币一注 ·
                      同一"翻倍或归零"结构下只改杠杆
                    </p>
                    <div className="mt-2 overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b bg-muted/40 text-left text-muted-foreground">
                            <th className="px-2 py-2 font-medium">杠杆</th>
                            <th className="px-2 py-2 font-medium text-right">止盈(翻倍价动)</th>
                            <th className="px-2 py-2 font-medium text-right">爆仓距离</th>
                            <th className="px-2 py-2 font-medium text-right">注数</th>
                            <th className="px-2 py-2 font-medium text-right">翻倍</th>
                            <th className="px-2 py-2 font-medium text-right">爆仓</th>
                            <th className="px-2 py-2 font-medium text-right">时间出</th>
                            <th className="px-2 py-2 font-medium text-right">胜率</th>
                            <th className="px-2 py-2 font-medium text-right">期望 U/注</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...paper.listing.levs]
                            .sort((a, b) => b.lev - a.lev)
                            .map((lv) => (
                              <tr key={lv.lev} className="border-b border-border/40 last:border-0">
                                <td className="px-2 py-1.5 font-mono">
                                  {lv.lev}x
                                  {lv.lev === 100 && (
                                    <span className="ml-1.5 rounded bg-rose-500/10 px-1 py-0.5 text-[10px] text-rose-500">
                                      玩法默认
                                    </span>
                                  )}
                                </td>
                                <td className="px-2 py-1.5 text-right font-mono">+{lv.tp_pct}%</td>
                                <td className="px-2 py-1.5 text-right font-mono">-{lv.sl_pct}%</td>
                                <td className="px-2 py-1.5 text-right font-mono">{lv.n}</td>
                                <td className="px-2 py-1.5 text-right font-mono text-emerald-500">{lv.tp}</td>
                                <td className="px-2 py-1.5 text-right font-mono text-rose-500">{lv.sl}</td>
                                <td className="px-2 py-1.5 text-right font-mono">{lv.timeout}</td>
                                <td className="px-2 py-1.5 text-right font-mono">{lv.win_rate}%</td>
                                <td
                                  className={cn(
                                    "px-2 py-1.5 text-right font-mono",
                                    lv.ev >= 0 ? "text-emerald-500" : "text-rose-500"
                                  )}
                                >
                                  {lv.ev >= 0 ? "+" : ""}
                                  {lv.ev}
                                </td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="mt-2 rounded-md bg-rose-500/5 px-2.5 py-1.5 text-[11px] leading-relaxed text-rose-500/90">
                      结论：方向正确率仅 {listingPaper?.dir_correct_pct ?? "--"}%（≈抛硬币）——首日脉冲本身没有 edge；
                      100x 下 {lv100?.n ?? 0} 注里 {slRate100 ?? "—"}% 死在 {lv100?.sl_pct ?? "0.6"}% 插针，
                      期望 {ev100}。同结构降到 10x 期望才 ≈0。要玩：降杠杆 + 等机制性方向（脱锚/解锁/投票），别赌首日脉冲。
                    </p>
                  </div>
                )}

                {/* ---- squeeze 深负费率 ---- */}
                {paper?.squeeze && (
                  <div className="rounded-lg border bg-background p-3">
                    <h3 className="text-[13px] font-semibold">
                      深负费率 · "轧空候选"方向检验
                      <span className="ml-2 rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-500">
                        {paper.squeeze.events} 事件 × {paper.squeeze.coins} 币
                      </span>
                    </h3>
                    <p className="mt-1 text-xs text-muted-foreground">
                      8h 费率 ≤ {paper.squeeze.threshold_pct}% 结算后做多收益 vs 同期全样本基准——
                      深负到底是不是"轧空蓄势"？
                    </p>
                    <div className="mt-2 overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b bg-muted/40 text-left text-muted-foreground">
                            <th className="px-2 py-2 font-medium">持有</th>
                            <th className="px-2 py-2 font-medium text-right">胜率 (收益&gt;0)</th>
                            <th className="px-2 py-2 font-medium text-right">平均收益</th>
                            <th className="px-2 py-2 font-medium text-right">中位收益</th>
                          </tr>
                        </thead>
                        <tbody>
                          {["T+1", "T+2", "T+3"].map((h) => {
                            const hh = paper.squeeze?.horizons?.[h];
                            if (!hh) return null;
                            return (
                              <tr key={h} className="border-b border-border/40 last:border-0">
                                <td className="px-2 py-1.5 font-mono">{h}</td>
                                <td className="px-2 py-1.5 text-right font-mono">{hh.win_rate}%</td>
                                <td
                                  className={cn(
                                    "px-2 py-1.5 text-right font-mono",
                                    hh.avg_pct >= 0 ? "text-emerald-500" : "text-rose-500"
                                  )}
                                >
                                  {hh.avg_pct >= 0 ? "+" : ""}
                                  {hh.avg_pct}%
                                </td>
                                <td className="px-2 py-1.5 text-right font-mono">
                                  {hh.median_pct >= 0 ? "+" : ""}
                                  {hh.median_pct}%
                                </td>
                              </tr>
                            );
                          })}
                          {paper.squeeze.base_win_rate != null && (
                            <tr className="bg-muted/20">
                              <td className="px-2 py-1.5 font-mono text-muted-foreground">
                                基准(随机日做多)
                              </td>
                              <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">
                                {paper.squeeze.base_win_rate}%
                              </td>
                              <td className="px-2 py-1.5 text-right text-muted-foreground" colSpan={2}>
                                {paper.squeeze.base_windows ?? "--"} 个日窗口
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                    <p className="mt-2 rounded-md bg-amber-500/5 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-600/90">
                      结论：深负费率不是"轧空蓄势"，是下跌动量延续——事件后做多胜率 40% 低于基准 49%，
                      越深负越跌。触发器的"轧空候选"应理解为"空头趋势观察"，追多危险；
                      真要等点火，必须等动量衰竭 + 放量反包确认，费率负本身不是进场理由。
                    </p>
                  </div>
                )}

                {/* ---- resonance 多周期共振 ---- */}
                {paper?.resonance && (
                  <div className="rounded-lg border bg-background p-3">
                    <h3 className="text-[13px] font-semibold">
                      多周期 KDJ+MACD 共振 · 老币方向检验
                      <span className="ml-2 rounded-full bg-violet-500/10 px-2 py-0.5 text-[11px] text-violet-500">
                        3币 × {paper.resonance.days}天 · 双边成本 {paper.resonance.cost_pct}%
                      </span>
                    </h3>
                    <p className="mt-1 text-xs text-muted-foreground">
                      1m/5m/15m 三周期同刻 KDJ(9,3,3)+MACD(12,26,9) 共振（多头排列 = 理论买点）——
                      共振时刻进场持有 1h，比混杂时刻强吗？
                    </p>
                    <div className="mt-2 overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b bg-muted/40 text-left text-muted-foreground">
                            <th className="px-2 py-2 font-medium">币种</th>
                            <th className="px-2 py-2 font-medium text-right">多头共振占比</th>
                            <th className="px-2 py-2 font-medium text-right">多头1h胜率</th>
                            <th className="px-2 py-2 font-medium text-right">混杂基准胜率</th>
                            <th className="px-2 py-2 font-medium text-right">胜率差</th>
                            <th className="px-2 py-2 font-medium text-right">多头1h均值</th>
                            <th className="px-2 py-2 font-medium text-right">混杂均值</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(paper.resonance.coins).map(([inst, c]) => {
                            const h1 = c.holds?.["4"];
                            const lg = h1?.long;
                            const mx = h1?.mixed;
                            if (!lg || !mx) return null;
                            const diff = lg.win_rate - mx.win_rate;
                            return (
                              <tr key={inst} className="border-b border-border/40 last:border-0">
                                <td className="px-2 py-1.5 font-mono text-[13px] font-medium">{inst}</td>
                                <td className="px-2 py-1.5 text-right font-mono">
                                  {c.long_pct}% ({c.long_n})
                                </td>
                                <td className="px-2 py-1.5 text-right font-mono">{lg.win_rate}%</td>
                                <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">
                                  {mx.win_rate}%
                                </td>
                                <td
                                  className={cn(
                                    "px-2 py-1.5 text-right font-mono",
                                    diff >= 0 ? "text-emerald-500" : "text-rose-500"
                                  )}
                                >
                                  {diff >= 0 ? "+" : ""}
                                  {diff.toFixed(1)}pp
                                </td>
                                <td
                                  className={cn(
                                    "px-2 py-1.5 text-right font-mono",
                                    lg.avg_pct >= 0 ? "text-emerald-500" : "text-rose-500"
                                  )}
                                >
                                  {lg.avg_pct >= 0 ? "+" : ""}
                                  {lg.avg_pct}%
                                </td>
                                <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">
                                  {mx.avg_pct >= 0 ? "+" : ""}
                                  {mx.avg_pct}%
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    <p className="mt-2 rounded-md bg-rose-500/5 px-2.5 py-1.5 text-[11px] leading-relaxed text-rose-500/90">
                      结论：走势中性的老牌币（BTC/DOGE）共振无 edge 甚至跑输——KDJ/MACD 滞后，共振出现时一波已
                      走完，买在脉冲后段。TRUMP 表面正差要警惕：近 14 天恰逢单边趋势段，共振=滞后顺势（涨势里追多
                      当然赢），行情一旦反转就会像 DOGE 那样反噬；75 个信号样本也不足以排除行情 beta。共振至多当
                      "已有方向下的执行过滤器"，单独当入场系统，数据不支持（以老牌币结论为准）。
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      逐点核对图表（标注 K 线）：{paper.resonance.chart_file} · 买卖点明细：{paper.resonance.md_file}
                    </p>
                  </div>
                )}

                <p className="text-[11px] text-muted-foreground">
                  重跑：<span className="font-mono">python3 ~/.hermes/scripts/hunter_paper.py</span>（上新首日，秒级，行情缓存）·
                  <span className="font-mono">python3 ~/.hermes/scripts/hunter_paper_squeeze.py</span>（深负费率，约 1 分钟）
                </p>
              </div>
            )}
          </section>

          <p className="text-[11px] text-muted-foreground">
            数据本地存储于 ~/.vibe-trading/hunter_state.json · 与 Vibe 体系隔离 ·
            触发器 cron hunter-scout 每 8h 自动扫描新合约与费率异动
          </p>
        </>
      )}
    </div>
  );
}
