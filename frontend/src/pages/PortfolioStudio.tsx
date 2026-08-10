import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Loader2,
  Plus,
  Scale,
  Trash2,
  Wand2,
} from "lucide-react";
import {
  api,
  type PortfolioConstraintsResult,
  type PortfolioOptimizeResult,
  type PortfolioRebalanceNotes,
  type PortfolioXrayReport,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type TabKey = "xray" | "constraints" | "rebalance" | "optimize";

const TABS: { key: TabKey; icon: typeof Scale }[] = [
  { key: "xray", icon: BarChart3 },
  { key: "constraints", icon: Scale },
  { key: "rebalance", icon: ArrowRight },
  { key: "optimize", icon: Wand2 },
];

const TAB_KEYS: Record<
  TabKey,
  "portfolioStudio.tabXray" | "portfolioStudio.tabConstraints" | "portfolioStudio.tabRebalance" | "portfolioStudio.tabOptimize"
> = {
  xray: "portfolioStudio.tabXray",
  constraints: "portfolioStudio.tabConstraints",
  rebalance: "portfolioStudio.tabRebalance",
  optimize: "portfolioStudio.tabOptimize",
};

/** Deterministic LCG so the sample data is stable across re-renders. */
function lcg(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function dateAfter(start: string, days: number): string {
  const [y, m, d] = start.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

function randomPrices(days: number, seed: number, start = 100): number[] {
  const rnd = lcg(seed);
  const out: number[] = [];
  let price = start;
  for (let i = 0; i < days; i++) {
    price *= Math.exp(0.0005 + (rnd() - 0.5) * 0.02);
    out.push(Number(price.toFixed(4)));
  }
  return out;
}

function randomReturnPanelJson(days: number, seed: number, start = "2025-01-02"): string {
  const rnd = lcg(seed);
  const rows: string[] = [];
  for (let i = 0; i < days; i++) {
    const vals = ["AAA", "BBB", "CCC"]
      .map((sym) => `"${sym}":${(0.0005 + (rnd() - 0.5) * 0.02).toFixed(6)}`)
      .join(",");
    rows.push(`"${dateAfter(start, i)}":{${vals}}`);
  }
  return `{\n  ${rows.join(",\n  ")}\n}`;
}

function positionPanelJson(days: number, start = "2025-01-02"): string {
  const rows: string[] = [];
  for (let i = 0; i < days; i++) {
    rows.push(`"${dateAfter(start, i)}":{"AAA":1,"BBB":0.5,"CCC":0.25}`);
  }
  return `{\n  ${rows.join(",\n  ")}\n}`;
}

const XRAY_EXAMPLE = () => {
  const days = 60;
  return [
    { id: 1, symbol: "AAA", weight: "50", prices: randomPrices(days, 7, 100).join(", ") },
    { id: 2, symbol: "BBB", weight: "30", prices: randomPrices(days, 11, 80).join(", ") },
    { id: 3, symbol: "CCC", weight: "20", prices: randomPrices(days, 13, 120).join(", ") },
  ];
};

const CONSTRAINTS_FRAME_EXAMPLE = `{
  "2025-01-02": {"AAA": 0.40, "BBB": 0.35, "CCC": 0.25},
  "2025-01-09": {"AAA": 0.30, "BBB": 0.40, "CCC": 0.30},
  "2025-01-16": {"AAA": 0.50, "BBB": 0.25, "CCC": 0.25}
}`;

const CONSTRAINTS_SPEC_EXAMPLE = `[
  {"type": "max_weight", "cap": 0.25},
  {"type": "group_exposure",
   "groups": {"AAA": "tech", "BBB": "tech", "CCC": "energy"},
   "caps": {"tech": 0.4, "energy": 0.4}}
]`;

const REBALANCE_EXAMPLE = `{
  "2025-01-02": {"AAA": 0.50, "BBB": 0.30, "CCC": 0.20},
  "2025-01-09": {"AAA": 0.30, "BBB": 0.50, "CCC": 0.20},
  "2025-01-16": {"AAA": 0.00, "BBB": 0.60, "CCC": 0.40}
}`;

function parseJson(text: string): Record<string, unknown> {
  return JSON.parse(text) as Record<string, unknown>;
}

function summaryTile(label: string, value: string, sub?: string) {
  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1.5 text-lg font-semibold tabular-nums">{value}</div>
      {sub ? <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div> : null}
    </div>
  );
}

function pct(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function runButton(loading: boolean, label: string) {
  return (
    <button
      type="submit"
      disabled={loading}
      className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <BarChart3 className="h-4 w-4" />}
      {label}
    </button>
  );
}

function errorBanner(error: string | null) {
  if (!error) return null;
  return (
    <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span className="break-all">{error}</span>
    </div>
  );
}

function SectionCard({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border/60 bg-card p-4">
      <div className="mb-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Tab: Risk X-Ray
// ---------------------------------------------------------------------------

interface AssetRow {
  id: number;
  symbol: string;
  weight: string;
  prices: string;
}

function XrayTab({ t }: { t: TFunction }) {
  const [rows, setRows] = useState<AssetRow[]>(XRAY_EXAMPLE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<PortfolioXrayReport | null>(null);

  const updateRow = (id: number, patch: Partial<AssetRow>) => {
    setRows((prev) => prev.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  };

  const handleRun = async () => {
    setLoading(true);
    setError(null);
    setReport(null);
    const closes: Record<string, number[]> = {};
    const weights: Record<string, number> = {};
    for (const row of rows) {
      const symbol = row.symbol.trim().toUpperCase();
      if (!symbol) continue;
      const prices = row.prices
        .split(/[\s,;]+/)
        .map(Number)
        .filter(Number.isFinite);
      if (prices.length > 0) closes[symbol] = prices;
      const weight = Number(row.weight);
      if (Number.isFinite(weight) && weight > 0) weights[symbol] = weight;
    }
    try {
      const next = await api.portfolioXray({ closes, weights });
      setReport(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("portfolioStudio.error"));
    } finally {
      setLoading(false);
    }
  };

  const r = report;
  const weightRows = r ? Object.entries(r.inputs.weights) : [];

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <SectionCard title={t("portfolioStudio.xrayInputTitle")} hint={t("portfolioStudio.xrayHint")}>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void handleRun();
          }}
        >
          <div className="grid grid-cols-[1fr_4.5rem_1fr_2rem] items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">{t("portfolioStudio.symbolCol")}</span>
            <span className="text-xs font-medium text-muted-foreground">{t("portfolioStudio.weightCol")}</span>
            <span className="text-xs font-medium text-muted-foreground">{t("portfolioStudio.pricesCol")}</span>
            <span />
            {rows.map((row) => (
              <FragmentRow
                key={row.id}
                row={row}
                update={updateRow}
                remove={() => setRows((prev) => prev.filter((x) => x.id !== row.id))}
                t={t}
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() =>
                setRows((prev) => [
                  ...prev,
                  { id: Date.now(), symbol: "", weight: "", prices: "" },
                ])
              }
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/60 px-3 text-xs font-medium transition hover:border-primary/50"
            >
              <Plus className="h-3.5 w-3.5" />
              {t("portfolioStudio.addAsset")}
            </button>
            <button
              type="button"
              onClick={() => setRows(XRAY_EXAMPLE)}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/60 px-3 text-xs font-medium transition hover:border-primary/50"
            >
              <Wand2 className="h-3.5 w-3.5" />
              {t("portfolioStudio.loadExample")}
            </button>
            <div className="ml-auto">{runButton(loading, t("portfolioStudio.run"))}</div>
          </div>
        </form>
      </SectionCard>

      <div className="flex flex-col gap-4">
        {errorBanner(error)}
        {r ? (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {summaryTile(t("portfolioStudio.annualizedVol"), pct(r.volatility.annualized_vol))}
              {summaryTile(t("portfolioStudio.maxDrawdown"), pct(r.drawdown.max_drawdown))}
              {summaryTile(t("portfolioStudio.var95"), pct(r.tail_risk.var_95))}
              {summaryTile(t("portfolioStudio.es95"), pct(r.tail_risk.expected_shortfall_95))}
              {summaryTile(
                t("portfolioStudio.effectiveN"),
                r.concentration.effective_n != null ? r.concentration.effective_n.toFixed(2) : "—",
              )}
              {summaryTile(
                t("portfolioStudio.divRatio"),
                r.diversification.diversification_ratio != null
                  ? r.diversification.diversification_ratio.toFixed(2)
                  : "—",
              )}
            </div>

            <SectionCard
              title={t("portfolioStudio.xrayDetailTitle")}
              hint={`${r.inputs.first_date} → ${r.inputs.last_date} · ${t("portfolioStudio.alignedDays")} ${r.inputs.aligned_days} · ${t("portfolioStudio.observations")} ${r.inputs.return_observations}`}
            >
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
                <DetailItem label={t("portfolioStudio.dailyVol")} value={pct(r.volatility.daily_vol)} />
                <DetailItem
                  label={t("portfolioStudio.downsideDev")}
                  value={pct(r.volatility.downside_deviation_annualized)}
                />
                <DetailItem label={t("portfolioStudio.hhi")} value={r.concentration.hhi?.toFixed(4) ?? "—"} />
                <DetailItem label={t("portfolioStudio.top1Weight")} value={pct(r.concentration.top1_weight)} />
                <DetailItem label={t("portfolioStudio.top3Weight")} value={pct(r.concentration.top3_weight)} />
                <DetailItem label={t("portfolioStudio.var99")} value={pct(r.tail_risk.var_99)} />
                <DetailItem label={t("portfolioStudio.es99")} value={pct(r.tail_risk.expected_shortfall_99)} />
                <DetailItem
                  label={t("portfolioStudio.avgPairCorr")}
                  value={r.correlation.avg_pairwise_abs != null ? r.correlation.avg_pairwise_abs.toFixed(3) : "—"}
                />
                <DetailItem
                  label={t("portfolioStudio.beta")}
                  value={r.correlation.beta_to_equal_weight != null ? r.correlation.beta_to_equal_weight.toFixed(3) : "—"}
                />
                <DetailItem
                  label={t("portfolioStudio.maxPair")}
                  value={
                    r.correlation.max_pair
                      ? `${r.correlation.max_pair.symbols.join("/")} ${r.correlation.max_pair.corr.toFixed(2)}`
                      : "—"
                  }
                />
                <DetailItem
                  label={t("portfolioStudio.drawdownWindow")}
                  value={
                    r.drawdown.max_drawdown_start && r.drawdown.max_drawdown_trough
                      ? `${r.drawdown.max_drawdown_start} → ${r.drawdown.max_drawdown_trough}`
                      : "—"
                  }
                />
                <DetailItem label={t("portfolioStudio.tailMethod")} value={r.tail_risk.method} />
              </dl>
            </SectionCard>

            {weightRows.length > 0 ? (
              <SectionCard title={t("portfolioStudio.weightsTitle")} hint="">
                <div className="flex flex-wrap gap-2">
                  {weightRows.map(([symbol, weight]) => (
                    <span
                      key={symbol}
                      className="inline-flex items-center gap-1.5 rounded-md border border-border/60 px-2.5 py-1 font-mono text-xs"
                    >
                      {symbol}
                      <span className="text-muted-foreground">{(weight * 100).toFixed(1)}%</span>
                    </span>
                  ))}
                </div>
              </SectionCard>
            ) : null}

            {r.skipped.length > 0 ? (
              <SectionCard title={t("portfolioStudio.skippedTitle")} hint="">
                <ul className="space-y-1 text-sm">
                  {r.skipped.map((s) => (
                    <li key={s.symbol} className="text-muted-foreground">
                      <span className="font-mono">{s.symbol}</span> — {s.reason}
                    </li>
                  ))}
                </ul>
              </SectionCard>
            ) : null}

            {r.warnings.length > 0 ? (
              <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-600">
                {r.warnings.join("; ")}
              </div>
            ) : null}
          </>
        ) : (
          <div className="rounded-lg border border-dashed border-border/60 p-8 text-center text-sm text-muted-foreground">
            {t("portfolioStudio.xrayEmpty")}
          </div>
        )}
      </div>
    </div>
  );
}

function FragmentRow({
  row,
  update,
  remove,
  t,
}: {
  row: AssetRow;
  update: (id: number, patch: Partial<AssetRow>) => void;
  remove: () => void;
  t: TFunction;
}) {
  return (
    <>
      <input
        value={row.symbol}
        onChange={(e) => update(row.id, { symbol: e.target.value })}
        placeholder="AAPL"
        className="h-9 w-full rounded-md border border-border/60 bg-background px-2.5 font-mono text-sm outline-none transition focus:border-primary/50"
      />
      <input
        value={row.weight}
        onChange={(e) => update(row.id, { weight: e.target.value })}
        placeholder="25"
        inputMode="decimal"
        className="h-9 w-full rounded-md border border-border/60 bg-background px-2.5 font-mono text-sm outline-none transition focus:border-primary/50"
      />
      <textarea
        value={row.prices}
        onChange={(e) => update(row.id, { prices: e.target.value })}
        placeholder="100, 100.5, 101.2, ..."
        rows={2}
        className="h-9 min-h-9 w-full resize-y rounded-md border border-border/60 bg-background px-2.5 py-1.5 font-mono text-xs outline-none transition focus:border-primary/50"
      />
      <button
        type="button"
        onClick={remove}
        aria-label={t("portfolioStudio.removeAsset")}
        className="inline-flex h-9 w-8 items-center justify-center rounded-md text-muted-foreground transition hover:bg-destructive/10 hover:text-destructive"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </>
  );
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-mono text-sm tabular-nums">{value}</dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Constraints
// ---------------------------------------------------------------------------

function ConstraintsTab({ t }: { t: TFunction }) {
  const [frameText, setFrameText] = useState(CONSTRAINTS_FRAME_EXAMPLE);
  const [specText, setSpecText] = useState(CONSTRAINTS_SPEC_EXAMPLE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PortfolioConstraintsResult | null>(null);

  const handleRun = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const frame = parseJson(frameText) as Record<string, Record<string, number>>;
      const constraints = parseJson(specText) as unknown as Record<string, unknown>[];
      const next = await api.portfolioApplyConstraints({ frame, constraints });
      setResult(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("portfolioStudio.error"));
    } finally {
      setLoading(false);
    }
  };

  const dateKeys = result ? Object.keys(result.frame) : [];

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <SectionCard
        title={t("portfolioStudio.constraintsInputTitle")}
        hint={t("portfolioStudio.constraintsHint")}
      >
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void handleRun();
          }}
        >
          <label className="text-xs font-medium text-muted-foreground">
            {t("portfolioStudio.frameLabel")}
            <textarea
              value={frameText}
              onChange={(e) => setFrameText(e.target.value)}
              rows={6}
              spellCheck={false}
              className="mt-1 w-full rounded-md border border-border/60 bg-background px-2.5 py-2 font-mono text-xs outline-none transition focus:border-primary/50"
            />
          </label>
          <label className="text-xs font-medium text-muted-foreground">
            {t("portfolioStudio.constraintsLabel")}
            <textarea
              value={specText}
              onChange={(e) => setSpecText(e.target.value)}
              rows={6}
              spellCheck={false}
              className="mt-1 w-full rounded-md border border-border/60 bg-background px-2.5 py-2 font-mono text-xs outline-none transition focus:border-primary/50"
            />
          </label>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setFrameText(CONSTRAINTS_FRAME_EXAMPLE);
                setSpecText(CONSTRAINTS_SPEC_EXAMPLE);
              }}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/60 px-3 text-xs font-medium transition hover:border-primary/50"
            >
              <Wand2 className="h-3.5 w-3.5" />
              {t("portfolioStudio.loadExample")}
            </button>
            <div className="ml-auto">{runButton(loading, t("portfolioStudio.run"))}</div>
          </div>
        </form>
      </SectionCard>

      <div className="flex flex-col gap-4">
        {errorBanner(error)}
        {result ? (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {summaryTile(t("portfolioStudio.adjustedCells"), String(result.summary.adjusted_cells))}
              {summaryTile(t("portfolioStudio.resultDates"), String(result.summary.dates))}
              {summaryTile(t("portfolioStudio.resultAssets"), String(result.summary.assets.length))}
              {summaryTile(
                t("portfolioStudio.constraintsApplied"),
                String(result.summary.constraints.length),
              )}
            </div>
            {result.summary.constraints.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {result.summary.constraints.map((spec) => (
                  <span
                    key={spec}
                    className="inline-flex items-center gap-1 rounded-md border border-border/60 px-2 py-1 font-mono text-xs text-muted-foreground"
                  >
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                    {spec}
                  </span>
                ))}
              </div>
            ) : null}
            <SectionCard title={t("portfolioStudio.before")} hint="">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-border/60 text-muted-foreground">
                    <th className="py-1.5 pr-2 font-medium">{t("portfolioStudio.dateCol")}</th>
                    <th className="py-1.5 pr-2 font-medium">{t("portfolioStudio.assetCol")}</th>
                    <th className="py-1.5 font-medium">{t("portfolioStudio.weightCol")}</th>
                  </tr>
                </thead>
                <tbody>
                  {dateKeys.map((date) =>
                    Object.entries(frameFromText(frameText)[date] ?? {}).map(([symbol, weight]) => (
                      <tr key={`${date}-${symbol}`} className="border-b border-border/30">
                        <td className="py-1.5 pr-2 font-mono">{date}</td>
                        <td className="py-1.5 pr-2 font-mono">{symbol}</td>
                        <td className="py-1.5 font-mono tabular-nums">{Number(weight).toFixed(4)}</td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </SectionCard>
            <SectionCard title={t("portfolioStudio.after")} hint="">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-border/60 text-muted-foreground">
                    <th className="py-1.5 pr-2 font-medium">{t("portfolioStudio.dateCol")}</th>
                    <th className="py-1.5 pr-2 font-medium">{t("portfolioStudio.assetCol")}</th>
                    <th className="py-1.5 font-medium">{t("portfolioStudio.weightCol")}</th>
                  </tr>
                </thead>
                <tbody>
                  {dateKeys.map((date) =>
                    Object.entries(result.frame[date] ?? {}).map(([symbol, weight]) => (
                      <tr key={`${date}-${symbol}`} className="border-b border-border/30">
                        <td className="py-1.5 pr-2 font-mono">{date}</td>
                        <td className="py-1.5 pr-2 font-mono">{symbol}</td>
                        <td className="py-1.5 font-mono tabular-nums">{Number(weight).toFixed(4)}</td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </SectionCard>
          </>
        ) : (
          <div className="rounded-lg border border-dashed border-border/60 p-8 text-center text-sm text-muted-foreground">
            {t("portfolioStudio.constraintsEmpty")}
          </div>
        )}
      </div>
    </div>
  );
}

function frameFromText(text: string): Record<string, Record<string, number>> {
  try {
    return JSON.parse(text) as Record<string, Record<string, number>>;
  } catch {
    return {};
  }
}

// ---------------------------------------------------------------------------
// Tab: Rebalance Notes
// ---------------------------------------------------------------------------

function RebalanceTab({ t }: { t: TFunction }) {
  const [posText, setPosText] = useState(REBALANCE_EXAMPLE);
  const [topN, setTopN] = useState("5");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<PortfolioRebalanceNotes | null>(null);

  const handleRun = async () => {
    setLoading(true);
    setError(null);
    setNotes(null);
    try {
      const target_pos = parseJson(posText) as Record<string, Record<string, number>>;
      const next = await api.portfolioRebalanceNotes({
        target_pos,
        top_n: Math.max(1, Number(topN) || 5),
      });
      setNotes(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("portfolioStudio.error"));
    } finally {
      setLoading(false);
    }
  };

  const s = notes?.summary;

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <SectionCard title={t("portfolioStudio.rebalanceInputTitle")} hint={t("portfolioStudio.rebalanceHint")}>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void handleRun();
          }}
        >
          <label className="text-xs font-medium text-muted-foreground">
            {t("portfolioStudio.targetPosLabel")}
            <textarea
              value={posText}
              onChange={(e) => setPosText(e.target.value)}
              rows={8}
              spellCheck={false}
              className="mt-1 w-full rounded-md border border-border/60 bg-background px-2.5 py-2 font-mono text-xs outline-none transition focus:border-primary/50"
            />
          </label>
          <label className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            {t("portfolioStudio.topN")}
            <input
              value={topN}
              onChange={(e) => setTopN(e.target.value)}
              inputMode="numeric"
              className="h-8 w-20 rounded-md border border-border/60 bg-background px-2 font-mono text-sm outline-none transition focus:border-primary/50"
            />
          </label>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPosText(REBALANCE_EXAMPLE)}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/60 px-3 text-xs font-medium transition hover:border-primary/50"
            >
              <Wand2 className="h-3.5 w-3.5" />
              {t("portfolioStudio.loadExample")}
            </button>
            <div className="ml-auto">{runButton(loading, t("portfolioStudio.run"))}</div>
          </div>
        </form>
      </SectionCard>

      <div className="flex flex-col gap-4">
        {errorBanner(error)}
        {notes ? (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {summaryTile(t("portfolioStudio.rebalanceCount"), String(s?.rebalance_count ?? 0))}
              {summaryTile(
                t("portfolioStudio.turnoverTotal"),
                s ? (s.turnover_total * 100).toFixed(1) + "%" : "—",
              )}
              {summaryTile(
                t("portfolioStudio.turnoverMean"),
                s ? (s.turnover_mean * 100).toFixed(2) + "%" : "—",
              )}
              {summaryTile(
                t("portfolioStudio.turnoverMax"),
                s ? (s.turnover_max * 100).toFixed(1) + "%" : "—",
              )}
            </div>
            {notes.rebalances.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border/60 p-8 text-center text-sm text-muted-foreground">
                {t("portfolioStudio.rebalancesEmpty")}
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {notes.rebalances.map((rebalance) => (
                  <SectionCard
                    key={rebalance.date}
                    title={`${rebalance.date} · ${t("portfolioStudio.turnover")} ${(rebalance.turnover * 100).toFixed(2)}%`}
                    hint={
                      rebalance.entries.length || rebalance.exits.length
                        ? `${rebalance.entries.length ? t("portfolioStudio.entries") + ": " + rebalance.entries.map((e) => e.code).join(", ") : ""}${
                            rebalance.entries.length && rebalance.exits.length ? " · " : ""
                          }${rebalance.exits.length ? t("portfolioStudio.exits") + ": " + rebalance.exits.map((e) => e.code).join(", ") : ""}`
                        : ""
                    }
                  >
                    {rebalance.top_moves.length > 0 ? (
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="border-b border-border/60 text-muted-foreground">
                            <th className="py-1.5 pr-2 font-medium">{t("portfolioStudio.assetCol")}</th>
                            <th className="py-1.5 pr-2 font-medium">{t("portfolioStudio.fromCol")}</th>
                            <th className="py-1.5 pr-2 font-medium">{t("portfolioStudio.toCol")}</th>
                            <th className="py-1.5 font-medium">{t("portfolioStudio.deltaCol")}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rebalance.top_moves.map((move) => (
                            <tr key={move.code} className="border-b border-border/30">
                              <td className="py-1.5 pr-2 font-mono">{move.code}</td>
                              <td className="py-1.5 pr-2 font-mono tabular-nums">{(move.from * 100).toFixed(2)}%</td>
                              <td className="py-1.5 pr-2 font-mono tabular-nums">{(move.to * 100).toFixed(2)}%</td>
                              <td
                                className={cn(
                                  "py-1.5 font-mono tabular-nums",
                                  move.delta > 0 ? "text-emerald-600" : "text-destructive",
                                )}
                              >
                                {(move.delta * 100).toFixed(2)}%
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : null}
                  </SectionCard>
                ))}
              </div>
            )}
          </>
        ) : (
          <div className="rounded-lg border border-dashed border-border/60 p-8 text-center text-sm text-muted-foreground">
            {t("portfolioStudio.rebalanceEmpty")}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Optimize
// ---------------------------------------------------------------------------

function OptimizeTab({ t }: { t: TFunction }) {
  const [returnsText, setReturnsText] = useState(() => randomReturnPanelJson(25, 3));
  const [positionsText, setPositionsText] = useState(() => positionPanelJson(25));
  const [lookback, setLookback] = useState("20");
  const [riskAversion, setRiskAversion] = useState("1.0");
  const [turnoverPenalty, setTurnoverPenalty] = useState("0.05");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PortfolioOptimizeResult | null>(null);

  const handleRun = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const returns = parseJson(returnsText) as Record<string, Record<string, number>>;
      const positions = parseJson(positionsText) as Record<string, Record<string, number>>;
      const next = await api.portfolioOptimize({
        returns,
        positions,
        lookback: Math.max(5, Number(lookback) || 20),
        risk_aversion: Number(riskAversion) || 1.0,
        turnover_penalty: Number(turnoverPenalty) || 0.0,
      });
      setResult(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("portfolioStudio.error"));
    } finally {
      setLoading(false);
    }
  };

  const rows = useMemo(() => {
    if (!result) return [];
    const dates = Object.keys(result.frame);
    return dates.slice(-5).map((date) => ({ date, cells: result.frame[date] }));
  }, [result]);

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <SectionCard title={t("portfolioStudio.optimizeInputTitle")} hint={t("portfolioStudio.optimizeHint")}>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void handleRun();
          }}
        >
          <label className="text-xs font-medium text-muted-foreground">
            {t("portfolioStudio.returnsLabel")}
            <textarea
              value={returnsText}
              onChange={(e) => setReturnsText(e.target.value)}
              rows={7}
              spellCheck={false}
              className="mt-1 w-full rounded-md border border-border/60 bg-background px-2.5 py-2 font-mono text-xs outline-none transition focus:border-primary/50"
            />
          </label>
          <label className="text-xs font-medium text-muted-foreground">
            {t("portfolioStudio.positionsLabel")}
            <textarea
              value={positionsText}
              onChange={(e) => setPositionsText(e.target.value)}
              rows={7}
              spellCheck={false}
              className="mt-1 w-full rounded-md border border-border/60 bg-background px-2.5 py-2 font-mono text-xs outline-none transition focus:border-primary/50"
            />
          </label>
          <div className="grid grid-cols-3 gap-2">
            <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
              {t("portfolioStudio.lookback")}
              <input
                value={lookback}
                onChange={(e) => setLookback(e.target.value)}
                inputMode="numeric"
                className="h-8 rounded-md border border-border/60 bg-background px-2 font-mono text-sm outline-none transition focus:border-primary/50"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
              {t("portfolioStudio.riskAversion")}
              <input
                value={riskAversion}
                onChange={(e) => setRiskAversion(e.target.value)}
                inputMode="decimal"
                className="h-8 rounded-md border border-border/60 bg-background px-2 font-mono text-sm outline-none transition focus:border-primary/50"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
              {t("portfolioStudio.turnoverPenalty")}
              <input
                value={turnoverPenalty}
                onChange={(e) => setTurnoverPenalty(e.target.value)}
                inputMode="decimal"
                className="h-8 rounded-md border border-border/60 bg-background px-2 font-mono text-sm outline-none transition focus:border-primary/50"
              />
            </label>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setReturnsText(randomReturnPanelJson(25, 3));
                setPositionsText(positionPanelJson(25));
              }}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/60 px-3 text-xs font-medium transition hover:border-primary/50"
            >
              <Wand2 className="h-3.5 w-3.5" />
              {t("portfolioStudio.loadExample")}
            </button>
            <div className="ml-auto">{runButton(loading, t("portfolioStudio.run"))}</div>
          </div>
        </form>
      </SectionCard>

      <div className="flex flex-col gap-4">
        {errorBanner(error)}
        {result ? (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {summaryTile(t("portfolioStudio.resultDates"), String(result.summary.dates))}
              {summaryTile(t("portfolioStudio.resultAssets"), String(result.summary.assets.length))}
              {summaryTile(t("portfolioStudio.lookback"), String(result.summary.lookback))}
              {summaryTile(
                t("portfolioStudio.turnoverPenalty"),
                String(result.summary.turnover_penalty),
              )}
            </div>
            <SectionCard title={t("portfolioStudio.lastRows")} hint="">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-border/60 text-muted-foreground">
                    <th className="py-1.5 pr-2 font-medium">{t("portfolioStudio.dateCol")}</th>
                    {result.summary.assets.map((asset) => (
                      <th key={asset} className="py-1.5 pr-2 font-medium">
                        {asset}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.date} className="border-b border-border/30">
                      <td className="py-1.5 pr-2 font-mono">{row.date}</td>
                      {result.summary.assets.map((asset) => (
                        <td key={asset} className="py-1.5 font-mono tabular-nums">
                          {row.cells[asset] != null ? Number(row.cells[asset]).toFixed(4) : "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </SectionCard>
          </>
        ) : (
          <div className="rounded-lg border border-dashed border-border/60 p-8 text-center text-sm text-muted-foreground">
            {t("portfolioStudio.optimizeEmpty")}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function PortfolioStudio() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TabKey>("xray");

  const tabProps = { t };

  return (
    <div className="min-h-screen p-6 lg:p-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <section className="flex flex-col gap-4 border-b border-border/60 pb-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-3">
            <div className="inline-flex items-center gap-2 rounded-md border border-border/60 px-2.5 py-1 text-xs font-medium text-muted-foreground">
              <Scale className="h-3.5 w-3.5" />
              {t("portfolioStudio.badge")}
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{t("portfolioStudio.title")}</h1>
              <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                {t("portfolioStudio.subtitle")} <span className="font-mono">/api/portfolio</span>
              </p>
            </div>
          </div>
        </section>

        <div className="flex flex-wrap gap-2">
          {TABS.map(({ key, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={cn(
                "inline-flex h-9 items-center gap-2 rounded-md border px-4 text-sm font-medium transition",
                tab === key
                  ? "border-primary/60 bg-primary/10 text-primary"
                  : "border-border/60 text-muted-foreground hover:border-primary/40 hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              {t(TAB_KEYS[key])}
            </button>
          ))}
        </div>

        {tab === "xray" ? <XrayTab {...tabProps} /> : null}
        {tab === "constraints" ? <ConstraintsTab {...tabProps} /> : null}
        {tab === "rebalance" ? <RebalanceTab {...tabProps} /> : null}
        {tab === "optimize" ? <OptimizeTab {...tabProps} /> : null}
      </div>
    </div>
  );
}
