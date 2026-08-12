import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  AlertTriangle,
  ArrowRight,
  LineChart,
  Loader2,
  Search,
  Sigma,
} from "lucide-react";
import {
  api,
  type OptionsChain,
  type OptionsPayoff,
  type OptionsPayoffParams,
  type OptionsSurface,
} from "@/lib/api";
import { VolSurfaceChart } from "@/components/charts/VolSurfaceChart";
import { cn } from "@/lib/utils";

/** Payoff analyzer strategy templates, in display order. */
const PAYOFF_STRATEGIES: Array<{
  id: OptionsPayoffParams["strategy"];
  labelKey: string;
}> = [
  { id: "bull_call_spread", labelKey: "optionsLab.strategyBullCallSpread" },
  { id: "long_straddle", labelKey: "optionsLab.strategyLongStraddle" },
  { id: "iron_condor", labelKey: "optionsLab.strategyIronCondor" },
];

interface PayoffFormState {
  lowerStrike: string;
  upperStrike: string;
  strike: string;
  putWing: string;
  putBody: string;
  callBody: string;
  callWing: string;
  qty: string;
  entrySpot: string;
}

/** Which strike inputs each strategy template requires. */
const PAYOFF_FIELDS: Record<
  OptionsPayoffParams["strategy"],
  Array<{ key: keyof PayoffFormState; labelKey: string }>
> = {
  bull_call_spread: [
    { key: "lowerStrike", labelKey: "optionsLab.lowerStrike" },
    { key: "upperStrike", labelKey: "optionsLab.upperStrike" },
  ],
  long_straddle: [{ key: "strike", labelKey: "optionsLab.strike" }],
  iron_condor: [
    { key: "putWing", labelKey: "optionsLab.putWing" },
    { key: "putBody", labelKey: "optionsLab.putBody" },
    { key: "callBody", labelKey: "optionsLab.callBody" },
    { key: "callWing", labelKey: "optionsLab.callWing" },
  ],
};

export function OptionsLab() {
  const { t } = useTranslation();
  const [tickerInput, setTickerInput] = useState("SPY");
  const [ticker, setTicker] = useState<string | null>(null);
  const [surface, setSurface] = useState<OptionsSurface | null>(null);
  const [chain, setChain] = useState<OptionsChain | null>(null);
  const [selectedExpiration, setSelectedExpiration] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [chainLoading, setChainLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(false);

  // Payoff analyzer state (pure local math — no market data required).
  const [payoffStrategy, setPayoffStrategy] =
    useState<OptionsPayoffParams["strategy"]>("bull_call_spread");
  const [payoffForm, setPayoffForm] = useState<PayoffFormState>({
    lowerStrike: "95",
    upperStrike: "105",
    strike: "100",
    putWing: "90",
    putBody: "95",
    callBody: "105",
    callWing: "110",
    qty: "1",
    entrySpot: "",
  });
  const [payoff, setPayoff] = useState<OptionsPayoff | null>(null);
  const [payoffLoading, setPayoffLoading] = useState(false);
  const [payoffError, setPayoffError] = useState<string | null>(null);

  const loadChain = useCallback(async (symbol: string, expiration: number) => {
    setChainLoading(true);
    try {
      const next = await api.getOptionsChain(symbol, expiration);
      if (!mountedRef.current) return;
      setChain(next);
    } catch (err) {
      if (!mountedRef.current) return;
      console.warn("Failed to load options chain", err);
      setChain(null);
    } finally {
      if (mountedRef.current) setChainLoading(false);
    }
  }, []);

  const loadSurface = useCallback(
    async (symbol: string) => {
      setLoading(true);
      setError(null);
      try {
        const next = await api.getOptionsSurface(symbol);
        if (!mountedRef.current) return;
        setSurface(next);
        setChain(null);
        const first = next.expirations[0]?.expiration ?? null;
        setSelectedExpiration(first);
        if (first != null) await loadChain(symbol, first);
      } catch (err) {
        if (!mountedRef.current) return;
        console.warn("Failed to load options surface", err);
        setSurface(null);
        setChain(null);
        setError(err instanceof Error ? err.message : t("optionsLab.loadFailed"));
      } finally {
        if (mountedRef.current) setLoading(false);
      }
    },
    [loadChain, t],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const handleAnalyze = () => {
    const symbol = tickerInput.trim().toUpperCase();
    if (!symbol) return;
    setTicker(symbol);
    void loadSurface(symbol);
  };

  const handleExpirationSelect = (expiration: number) => {
    setSelectedExpiration(expiration);
    if (ticker) void loadChain(ticker, expiration);
  };

  const setPayoffField =
    (key: keyof PayoffFormState) => (event: ChangeEvent<HTMLInputElement>) => {
      setPayoffForm((prev) => ({ ...prev, [key]: event.target.value }));
    };

  const handleCalculatePayoff = useCallback(async () => {
    const toNum = (value: string): number | undefined => {
      if (value.trim() === "") return undefined;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : undefined;
    };
    const strikes: Partial<OptionsPayoffParams> = {};
    if (payoffStrategy === "bull_call_spread") {
      strikes.lowerStrike = toNum(payoffForm.lowerStrike);
      strikes.upperStrike = toNum(payoffForm.upperStrike);
    } else if (payoffStrategy === "long_straddle") {
      strikes.strike = toNum(payoffForm.strike);
    } else {
      strikes.putWing = toNum(payoffForm.putWing);
      strikes.putBody = toNum(payoffForm.putBody);
      strikes.callBody = toNum(payoffForm.callBody);
      strikes.callWing = toNum(payoffForm.callWing);
    }
    const qty = toNum(payoffForm.qty);
    const entrySpot = toNum(payoffForm.entrySpot);
    const params: OptionsPayoffParams = {
      strategy: payoffStrategy,
      ...strikes,
      ...(qty != null ? { qty: Math.max(1, Math.min(100, Math.round(qty))) } : {}),
      ...(entrySpot != null && entrySpot > 0 ? { entrySpot } : {}),
    };
    setPayoffLoading(true);
    setPayoffError(null);
    try {
      const next = await api.getOptionsPayoff(params);
      if (!mountedRef.current) return;
      setPayoff(next);
    } catch (err) {
      if (!mountedRef.current) return;
      console.warn("Failed to compute payoff", err);
      setPayoff(null);
      setPayoffError(err instanceof Error ? err.message : t("optionsLab.loadFailed"));
    } finally {
      if (mountedRef.current) setPayoffLoading(false);
    }
  }, [payoffStrategy, payoffForm, t]);

  const selectedExpiry =
    surface?.expirations.find((e) => e.expiration === selectedExpiration) ?? null;

  return (
    <div className="min-h-screen p-6 lg:p-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <section className="flex flex-col gap-4 border-b border-border/60 pb-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-3">
            <div className="inline-flex items-center gap-2 rounded-md border border-border/60 px-2.5 py-1 text-xs font-medium text-muted-foreground">
              <Sigma className="h-3.5 w-3.5" />
              {t("optionsLab.badge")}
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{t("optionsLab.title")}</h1>
              <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                {t("optionsLab.subtitle")} <span className="font-mono">/api/options-lab</span>
              </p>
            </div>
          </div>
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              handleAnalyze();
            }}
          >
            <input
              value={tickerInput}
              onChange={(event) => setTickerInput(event.target.value)}
              placeholder={t("optionsLab.tickerPlaceholder")}
              className="h-9 w-40 rounded-md border border-border/60 bg-card px-3 font-mono text-sm outline-none transition focus:border-primary/50"
            />
            <button
              type="submit"
              disabled={loading || !tickerInput.trim()}
              className="inline-flex h-9 items-center gap-2 rounded-md border border-border/60 bg-card px-4 text-sm font-medium transition hover:bg-muted/60 disabled:opacity-50"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
              {t("optionsLab.analyze")}
            </button>
          </form>
        </section>

        {error ? (
          <section className="rounded-xl border border-warning/30 bg-warning/5 p-5 shadow-sm">
            <div className="flex items-center gap-2 font-medium text-warning">
              <AlertTriangle className="h-5 w-5" />
              {t("optionsLab.loadFailed")}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">{error}</p>
          </section>
        ) : null}

        {!loading && surface ? (
          <>
            {/* Summary tiles */}
            <section className="grid gap-3 md:grid-cols-4">
              <SummaryTile
                label={t("optionsLab.spotLabel")}
                value={surface.spot != null ? `$${surface.spot.toLocaleString("en-US")}` : t("optionsLab.noSpot")}
                icon={LineChart}
              />
              <SummaryTile
                label={t("optionsLab.expirationsLabel")}
                value={String(surface.expirations.length)}
                icon={Sigma}
              />
              <SummaryTile
                label={t("optionsLab.atmIv")}
                value={
                  selectedExpiry?.atm_iv != null
                    ? `${(selectedExpiry.atm_iv * 100).toFixed(1)}%`
                    : t("optionsLab.noSpot")
                }
                icon={LineChart}
              />
              <SummaryTile
                label={t("optionsLab.skew")}
                value={formatSkew(selectedExpiry?.skew, t)}
                icon={ArrowRight}
              />
            </section>

            {/* Vol surface chart */}
            <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
              <div className="mb-3 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                <LineChart className="h-3.5 w-3.5" />
                {t("optionsLab.surfaceTitle")}
              </div>
              <VolSurfaceChart surface={surface} />
            </section>

            {/* Expiration chips */}
            <section className="flex flex-wrap gap-2">
              {surface.expirations.map((expiry) => (
                <button
                  key={expiry.expiration}
                  type="button"
                  onClick={() => handleExpirationSelect(expiry.expiration)}
                  className={cn(
                    "rounded-md border border-border/60 bg-card px-3 py-1.5 text-xs font-medium transition hover:bg-muted/60",
                    selectedExpiration === expiry.expiration &&
                      "border-primary/50 bg-muted",
                  )}
                >
                  {new Date(expiry.expiration * 1000).toLocaleDateString()}
                  <span className="ml-2 text-muted-foreground">
                    {Math.round(expiry.days_to_expiry)}
                    {t("optionsLab.daysSuffix")}
                  </span>
                  {expiry.skew != null ? (
                    <span
                      className={cn(
                        "ml-2",
                        expiry.skew > 0.005 ? "text-danger" : expiry.skew < -0.005 ? "text-success" : "text-muted-foreground",
                      )}
                    >
                      {expiry.skew > 0 ? "+" : ""}
                      {(expiry.skew * 100).toFixed(1)}
                    </span>
                  ) : null}
                </button>
              ))}
            </section>

            {/* Greeks ladder */}
            <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
              <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                <Sigma className="h-3.5 w-3.5" />
                {t("optionsLab.chainTitle")}
              </div>
              <p className="mb-3 text-xs text-muted-foreground">{t("optionsLab.chainHint")}</p>
              {chainLoading ? (
                <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("optionsLab.loading")}
                </div>
              ) : chain && chain.contracts.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border/60 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                        <th className="py-2 pr-3 font-medium">{t("optionsLab.colType")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("optionsLab.colStrike")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("optionsLab.colIv")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("optionsLab.colBid")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("optionsLab.colAsk")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("optionsLab.colLast")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("optionsLab.colOi")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("optionsLab.colDelta")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("optionsLab.colGamma")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("optionsLab.colTheta")}</th>
                        <th className="py-2 text-right font-medium">{t("optionsLab.colVega")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {chain.contracts.map((contract, index) => (
                        <tr
                          key={`${contract.type}-${contract.strike}-${index}`}
                          className="border-b border-border/40 last:border-0"
                        >
                          <td
                            className={cn(
                              "py-2 pr-3 font-medium",
                              contract.type === "call" ? "text-success" : "text-danger",
                            )}
                          >
                            {contract.type === "call" ? t("optionsLab.call") : t("optionsLab.put")}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">{contract.strike}</td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {contract.iv != null ? `${(contract.iv * 100).toFixed(1)}%` : "-"}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {contract.bid ?? "-"}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {contract.ask ?? "-"}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {contract.last ?? "-"}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {contract.open_interest ?? "-"}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {contract.delta != null ? contract.delta.toFixed(3) : "-"}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {contract.gamma != null ? contract.gamma.toFixed(4) : "-"}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {contract.theta != null ? contract.theta.toFixed(4) : "-"}
                          </td>
                          <td className="py-2 text-right font-mono">
                            {contract.vega != null ? contract.vega.toFixed(4) : "-"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="py-6 text-sm text-muted-foreground">{t("optionsLab.chainEmpty")}</p>
              )}
            </section>

            {/* Payoff analyzer (pure local math) */}
            <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
              <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                <LineChart className="h-3.5 w-3.5" />
                {t("optionsLab.payoffTitle")}
              </div>
              <p className="mb-4 text-xs text-muted-foreground">{t("optionsLab.payoffHint")}</p>

              <div className="flex flex-wrap gap-2">
                {PAYOFF_STRATEGIES.map((strategy) => (
                  <button
                    key={strategy.id}
                    type="button"
                    onClick={() => setPayoffStrategy(strategy.id)}
                    className={cn(
                      "rounded-md border border-border/60 bg-background px-3 py-1.5 text-xs font-medium transition hover:bg-muted/60",
                      payoffStrategy === strategy.id && "border-primary/50 bg-muted",
                    )}
                  >
                    {t(strategy.labelKey as never)}
                  </button>
                ))}
              </div>

              <div className="mt-4 flex flex-wrap items-end gap-3">
                {PAYOFF_FIELDS[payoffStrategy].map((field) => (
                  <label
                    key={field.key}
                    className="flex flex-col gap-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    {t(field.labelKey as never)}
                    <input
                      type="number"
                      min={0}
                      step="any"
                      value={payoffForm[field.key]}
                      onChange={setPayoffField(field.key)}
                      className="h-9 w-28 rounded-md border border-border/60 bg-background px-3 font-mono text-sm outline-none transition focus:border-primary/50"
                    />
                  </label>
                ))}
                <label className="flex flex-col gap-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {t("optionsLab.qty")}
                  <input
                    type="number"
                    min={1}
                    max={100}
                    step={1}
                    value={payoffForm.qty}
                    onChange={setPayoffField("qty")}
                    className="h-9 w-20 rounded-md border border-border/60 bg-background px-3 font-mono text-sm outline-none transition focus:border-primary/50"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {t("optionsLab.entrySpot")}
                  <input
                    type="number"
                    min={0}
                    step="any"
                    value={payoffForm.entrySpot}
                    onChange={setPayoffField("entrySpot")}
                    placeholder={t("optionsLab.noSpot")}
                    className="h-9 w-28 rounded-md border border-border/60 bg-background px-3 font-mono text-sm outline-none transition focus:border-primary/50"
                  />
                </label>
                <button
                  type="button"
                  disabled={payoffLoading}
                  onClick={() => void handleCalculatePayoff()}
                  className="inline-flex h-9 items-center gap-2 rounded-md border border-border/60 bg-background px-4 text-sm font-medium transition hover:bg-muted/60 disabled:opacity-50"
                >
                  {payoffLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  {t("optionsLab.calculate")}
                </button>
              </div>

              {payoffError ? (
                <p className="mt-3 text-sm text-danger">{payoffError}</p>
              ) : null}

              {payoff ? (
                <>
                  <div className="mt-5 grid gap-3 md:grid-cols-4">
                    <SummaryTile
                      label={t("optionsLab.netPremium")}
                      value={`$${payoff.net_premium.toLocaleString("en-US", { maximumFractionDigits: 2 })}`}
                      icon={LineChart}
                    />
                    <SummaryTile
                      label={t("optionsLab.maxProfit")}
                      value={formatMoney(payoff.max_profit, payoff.profit_unbounded, t)}
                      icon={ArrowRight}
                    />
                    <SummaryTile
                      label={t("optionsLab.maxLoss")}
                      value={formatMoney(payoff.max_loss, payoff.loss_unbounded, t)}
                      icon={AlertTriangle}
                    />
                    <SummaryTile
                      label={t("optionsLab.breakevens")}
                      value={
                        payoff.breakevens.length > 0
                          ? payoff.breakevens.map((b) => `$${b.toFixed(2)}`).join(" / ")
                          : "-"
                      }
                      icon={Sigma}
                    />
                  </div>
                  <div className="mt-4 overflow-x-auto">
                    <PayoffChart payoff={payoff} />
                  </div>
                </>
              ) : (
                <p className="py-6 text-sm text-muted-foreground">{t("optionsLab.payoffEmpty")}</p>
              )}
            </section>
          </>
        ) : null}
      </div>
    </div>
  );
}

function formatMoney(
  value: number | null,
  unbounded: boolean,
  t: TFunction,
): string {
  if (unbounded) return t("optionsLab.unbounded");
  if (value == null) return "-";
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

/** Inline SVG expiry payoff curve with zero line and breakeven markers. */
function PayoffChart({ payoff }: { payoff: OptionsPayoff }) {
  const width = 640;
  const height = 260;
  const padL = 64;
  const padR = 20;
  const padT = 20;
  const padB = 30;
  const curve = payoff.curve;
  if (curve.length < 2) return null;

  const spots = curve.map((p) => p.spot);
  const pnls = curve.map((p) => p.pnl);
  const minSpot = Math.min(...spots);
  const maxSpot = Math.max(...spots);
  const minPnl = Math.min(...pnls, 0);
  const maxPnl = Math.max(...pnls, 0);
  const spanSpot = maxSpot - minSpot || 1;
  const spanPnl = maxPnl - minPnl || 1;

  const x = (spot: number) => padL + ((spot - minSpot) / spanSpot) * (width - padL - padR);
  const y = (pnl: number) => padT + ((maxPnl - pnl) / spanPnl) * (height - padT - padB);
  const polyline = curve
    .map((p) => `${x(p.spot).toFixed(1)},${y(p.pnl).toFixed(1)}`)
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full min-w-[560px]"
      role="img"
      aria-label={`${payoff.strategy} expiry payoff`}
    >
      {/* Zero line */}
      <line
        x1={padL}
        y1={y(0)}
        x2={width - padR}
        y2={y(0)}
        className="stroke-muted"
        strokeDasharray="4 4"
        strokeWidth={1}
      />
      {/* Breakeven markers */}
      {payoff.breakevens.map((breakeven, index) => (
        <line
          key={index}
          x1={x(breakeven)}
          y1={padT}
          x2={x(breakeven)}
          y2={height - padB}
          className="stroke-warning"
          strokeDasharray="2 4"
          strokeWidth={1}
        />
      ))}
      {/* Payoff curve */}
      <polyline
        points={polyline}
        fill="none"
        className="stroke-primary"
        strokeWidth={2}
        strokeLinejoin="round"
      />
      {/* Y axis ticks */}
      {[minPnl, 0, maxPnl].map((value, index) => (
        <text
          key={index}
          x={padL - 8}
          y={y(value) + 3}
          textAnchor="end"
          className="fill-muted-foreground font-mono text-[10px]"
        >
          {value.toFixed(0)}
        </text>
      ))}
      {/* X axis ticks */}
      {[minSpot, payoff.entry_spot, maxSpot].map((value, index) => (
        <text
          key={index}
          x={x(value)}
          y={height - padB + 16}
          textAnchor="middle"
          className="fill-muted-foreground font-mono text-[10px]"
        >
          {value.toFixed(0)}
        </text>
      ))}
    </svg>
  );
}

function SummaryTile({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string;
  icon: typeof LineChart;
}) {
  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </div>
      <div className="mt-3 text-2xl font-semibold">{value}</div>
    </div>
  );
}

function formatSkew(
  skew: number | null | undefined,
  t: TFunction,
): string {
  if (skew == null) return "-";
  const value = `${skew > 0 ? "+" : ""}${(skew * 100).toFixed(1)}%`;
  if (skew > 0.005) return `${value} · ${t("optionsLab.skewNegative")}`;
  if (skew < -0.005) return `${value} · ${t("optionsLab.skewPositive")}`;
  return `${value} · ${t("optionsLab.skewFlat")}`;
}
