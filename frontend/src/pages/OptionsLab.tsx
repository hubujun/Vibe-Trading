import { useCallback, useEffect, useRef, useState } from "react";
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
import { api, type OptionsChain, type OptionsSurface } from "@/lib/api";
import { VolSurfaceChart } from "@/components/charts/VolSurfaceChart";
import { cn } from "@/lib/utils";

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
          </>
        ) : null}
      </div>
    </div>
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
