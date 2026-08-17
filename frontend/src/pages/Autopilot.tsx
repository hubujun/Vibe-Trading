import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Database,
  History,
  Layers,
  Loader2,
  OctagonX,
  Receipt,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Wifi,
  WifiOff,
} from "lucide-react";
import {
  api,
  type AutopilotDailyPnl,
  type AutopilotFactorList,
  type AutopilotPerformance,
  type AutopilotPosition,
  type AutopilotStatus,
  type AutopilotTradeRecord,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const AUTOPILOT_POLL_INTERVAL_MS = 15_000;
const AUTOPILOT_CLOCK_INTERVAL_MS = 1_000;

export function Autopilot() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<AutopilotStatus | null>(null);
  const [trades, setTrades] = useState<AutopilotTradeRecord[]>([]);
  const [factors, setFactors] = useState<AutopilotFactorList | null>(null);
  const [performance, setPerformance] = useState<AutopilotPerformance | null>(null);
  const [positions, setPositions] = useState<AutopilotPosition[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const activeRequestRef = useRef<{ id: number; controller: AbortController } | null>(null);
  const requestSeqRef = useRef(0);
  const mountedRef = useRef(false);

  const loadStatus = useCallback(async (mode: "initial" | "refresh" = "refresh") => {
    const requestId = requestSeqRef.current + 1;
    requestSeqRef.current = requestId;
    activeRequestRef.current?.controller.abort();
    const controller = new AbortController();
    activeRequestRef.current = { id: requestId, controller };

    if (mode === "initial") setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      // The trade ledger, factor snapshot, paper performance and open
      // positions are all best-effort: a failure must never take down the
      // status panel, so they are fetched in parallel and degrade silently.
      const [next, tradesResult, factorsResult, perfResult, positionsResult] =
        await Promise.all([
          api.getAutopilotStatus(controller.signal),
          api.getAutopilotTrades(controller.signal).catch(() => null),
          api.getAutopilotFactors(controller.signal).catch(() => null),
          api.getAutopilotPerformance(controller.signal).catch(() => null),
          api.getAutopilotPositions(controller.signal).catch(() => null),
        ]);
      if (!isCurrentRequest(activeRequestRef.current, requestId, controller)) return;
      setStatus(next);
      if (tradesResult) setTrades(tradesResult.trades);
      if (factorsResult) setFactors(factorsResult);
      if (perfResult) setPerformance(perfResult);
      if (positionsResult) setPositions(positionsResult.positions);
    } catch (err) {
      if (controller.signal.aborted) return;
      if (!isCurrentRequest(activeRequestRef.current, requestId, controller)) return;
      console.warn("Failed to load autopilot status", err);
      setStatus(null);
      setError(err instanceof Error ? err.message : t("autopilot.unavailableTitle"));
    } finally {
      if (!isCurrentRequest(activeRequestRef.current, requestId, controller)) return;
      activeRequestRef.current = null;
      setLoading(false);
      setRefreshing(false);
    }
  }, [t]);

  useEffect(() => {
    mountedRef.current = true;
    loadStatus("initial");
    const pollTimer = window.setInterval(() => loadStatus("refresh"), AUTOPILOT_POLL_INTERVAL_MS);
    const clockTimer = window.setInterval(() => setNowMs(Date.now()), AUTOPILOT_CLOCK_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      requestSeqRef.current += 1;
      activeRequestRef.current?.controller.abort();
      activeRequestRef.current = null;
      window.clearInterval(pollTimer);
      window.clearInterval(clockTimer);
    };
  }, [loadStatus]);

  const phase = status?.pipeline.phase ?? "idle";

  // Pipeline phase -> Chinese display label (backend emits snake_case ids).
  const PHASE_LABELS: Record<string, string> = {
    idle: "待机",
    collecting: "数据采集",
    discovering: "因子挖掘",
    backtesting: "回测评估",
    paper_trading: "模拟交易",
    live: "实盘交易",
    feedback: "复盘反馈",
  };
  const displayPhase = (p: string) => PHASE_LABELS[p] ?? p;

  const alive = status?.health.alive ?? false;
  const halted = status?.halt.halted ?? false;
  const orderRatio = status
    ? status.counter.count / Math.max(1, status.config.max_trades_per_day)
    : 0;

  return (
    <div className="min-h-screen p-6 lg:p-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <section className="flex flex-col gap-4 border-b border-border/60 pb-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-3">
            <div className="inline-flex items-center gap-2 rounded-md border border-border/60 px-2.5 py-1 text-xs font-medium text-muted-foreground">
              <Cpu className="h-3.5 w-3.5" />
              {t("autopilot.monitorBadge")}
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{t("autopilot.title")}</h1>
              <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                {t("autopilot.subtitlePre")} <span className="font-mono">/api/autopilot/status</span>
                {t("autopilot.subtitlePost")}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => loadStatus("refresh")}
            disabled={refreshing}
            className="inline-flex items-center gap-2 rounded-md border border-border/60 px-4 py-2 text-sm font-medium transition hover:bg-muted/60 disabled:opacity-50"
          >
            {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            {t("autopilot.refresh")}
          </button>
        </section>

        {loading ? (
          <div className="grid gap-3 md:grid-cols-4">
            {[1, 2, 3, 4].map((item) => (
              <div key={item} className="h-24 animate-pulse rounded-xl border border-border/60 bg-card shadow-sm" />
            ))}
          </div>
        ) : null}

        {!loading && error ? (
          <section className="rounded-xl border border-warning/30 bg-warning/5 p-5 shadow-sm">
            <div className="flex items-center gap-2 font-medium text-warning">
              <AlertTriangle className="h-5 w-5" />
              {t("autopilot.unavailableTitle")}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">{error}</p>
            <p className="mt-2 text-xs text-muted-foreground">{t("autopilot.unavailableHint")}</p>
          </section>
        ) : null}

        {!loading && !error && status ? (
          <>
            <section className="grid gap-3 md:grid-cols-4">
              <SummaryTile
                label={t("autopilot.phaseLabel")}
                value={displayPhase(phase)}
                tone={phase === "idle" ? "neutral" : phase === "live" ? "success" : "warning"}
                icon={Layers}
              />
              <SummaryTile
                label={t("autopilot.livenessLabel")}
                value={alive ? t("autopilot.running") : t("autopilot.offline")}
                tone={alive ? "success" : "danger"}
                icon={alive ? Wifi : WifiOff}
              />
              <SummaryTile
                label={t("autopilot.haltLabel")}
                value={halted ? t("autopilot.halted") : t("autopilot.clear")}
                tone={halted ? "danger" : "success"}
                icon={halted ? OctagonX : CheckCircle2}
              />
              <SummaryTile
                label={t("autopilot.ordersLabel")}
                value={`${status.counter.count} / ${status.config.max_trades_per_day}`}
                tone={orderRatio >= 1 ? "danger" : orderRatio >= 0.8 ? "warning" : "neutral"}
                icon={Receipt}
              />
            </section>

            <section className="grid gap-4 lg:grid-cols-2">
              <AutopilotPanel title={t("autopilot.pipelineTitle")} icon={Layers}>
                <KeyValue label={t("autopilot.phase")} value={displayPhase(phase)} />
                <KeyValue
                  label={t("autopilot.activeFactor")}
                  value={status.pipeline.active_factor_id || t("autopilot.none")}
                />
                <KeyValue label={t("autopilot.tickCount")} value={String(status.pipeline.tick_count)} />
                <KeyValue
                  label={t("autopilot.lastTick")}
                  value={formatTimestamp(status.pipeline.last_tick_at, t, nowMs)}
                />
                <KeyValue
                  label={t("autopilot.updatedAt")}
                  value={formatTimestamp(status.pipeline.updated_at, t, nowMs)}
                />
              </AutopilotPanel>

              <AutopilotPanel title={t("autopilot.healthTitle")} icon={alive ? Wifi : WifiOff}>
                <KeyValue
                  label={t("autopilot.alive")}
                  value={alive ? t("autopilot.yes") : t("autopilot.no")}
                />
                <KeyValue
                  label={t("autopilot.stale")}
                  value={status.health.stale ? t("autopilot.yes") : t("autopilot.no")}
                />
                <KeyValue
                  label={t("autopilot.lastHeartbeat")}
                  value={formatHeartbeatAge(status.health.heartbeat_ms, t, nowMs)}
                />
              </AutopilotPanel>

              <AutopilotPanel title={t("autopilot.haltTitle")} icon={halted ? OctagonX : ShieldCheck}>
                <KeyValue
                  label={t("autopilot.haltState")}
                  value={halted ? t("autopilot.halted") : t("autopilot.clear")}
                />
                <KeyValue label={t("autopilot.reason")} value={status.halt.reason || t("autopilot.none")} />
                <KeyValue label={t("autopilot.trippedBy")} value={status.halt.tripped_by || t("autopilot.none")} />
                <KeyValue
                  label={t("autopilot.trippedAt")}
                  value={formatTimestamp(status.halt.tripped_at, t, nowMs)}
                />
              </AutopilotPanel>

              <AutopilotPanel title={t("autopilot.counterTitle")} icon={Receipt}>
                <KeyValue label={t("autopilot.counterDate")} value={status.counter.date} />
                <div className="pt-1">
                  <div className="flex items-center justify-between text-[11px] uppercase text-muted-foreground">
                    <span>{t("autopilot.dailyProgress")}</span>
                    <span>
                      {status.counter.count} / {status.config.max_trades_per_day}
                    </span>
                  </div>
                  <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all",
                        orderRatio >= 1 ? "bg-danger" : orderRatio >= 0.8 ? "bg-warning" : "bg-success",
                      )}
                      style={{ width: `${Math.min(100, orderRatio * 100)}%` }}
                    />
                  </div>
                </div>
              </AutopilotPanel>

              <AutopilotPanel
                title={t("autopilot.dataHealthTitle")}
                icon={Database}
              >
                {status.data_health.stale_symbols.length > 0 ? (
                  <div className="flex items-center gap-2 text-xs font-medium text-warning">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    {t("autopilot.dataStale")}: {status.data_health.stale_symbols.join(", ")}
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-xs font-medium text-success">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    {t("autopilot.dataFresh")}
                  </div>
                )}
                <div className="mt-2 max-h-36 space-y-1 overflow-y-auto">
                  {Object.entries(status.data_health.symbols).map(([symbol, info]) => (
                    <div key={symbol} className="flex items-center justify-between gap-2 text-xs">
                      <span className="font-mono">{symbol}</span>
                      <span
                        className={cn(
                          "font-mono",
                          info.lag_hours != null && info.lag_hours > 2
                            ? "text-warning"
                            : "text-muted-foreground",
                        )}
                      >
                        {info.lag_hours != null
                          ? `${info.lag_hours.toFixed(1)}h`
                          : t("autopilot.none")}
                      </span>
                    </div>
                  ))}
                </div>
                <KeyValue
                  label={t("autopilot.dataUpdatedAt")}
                  value={formatTimestamp(status.data_health.updated_at, t, nowMs)}
                />
              </AutopilotPanel>

              <AutopilotPanel title={t("autopilot.configTitle")} icon={Activity} wide>
                <div className="grid gap-3 md:grid-cols-2">
                  <KeyValue label={t("autopilot.enabled")} value={status.config.enabled ? t("autopilot.yes") : t("autopilot.no")} />
                  <KeyValue label={t("autopilot.pairs")} value={status.config.pairs.join(", ")} />
                  <KeyValue
                    label={t("autopilot.mineInterval")}
                    value={`${status.config.mine_interval_hours}${t("autopilot.hoursSuffix")}`}
                  />
                  <KeyValue
                    label={t("autopilot.evaluateInterval")}
                    value={`${status.config.evaluate_interval_hours}${t("autopilot.hoursSuffix")}`}
                  />
                  <KeyValue
                    label={t("autopilot.tradeInterval")}
                    value={`${status.config.trade_interval_minutes}${t("autopilot.minutesSuffix")}`}
                  />
                  <KeyValue
                    label={t("autopilot.feedbackInterval")}
                    value={`${status.config.feedback_interval_hours}${t("autopilot.hoursSuffix")}`}
                  />
                  <KeyValue
                    label={t("autopilot.maxOrderNotional")}
                    value={`$${status.config.max_order_notional_usd.toLocaleString("en-US")}`}
                  />
                  <KeyValue
                    label={t("autopilot.maxExposure")}
                    value={`$${status.config.max_total_exposure_usd.toLocaleString("en-US")}`}
                  />
                </div>
              </AutopilotPanel>
            </section>

            <AutopilotPanel title={t("autopilot.factorsTitle")} icon={Sparkles} wide>
              {factors &&
              (factors.active.length > 0 ||
                factors.pending.length > 0 ||
                factors.zoo.length > 0 ||
                factors.retired.length > 0) ? (
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  <div>
                    <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("autopilot.factorActive")}
                    </div>
                    {factors.active.length === 0 ? (
                      <p className="text-sm text-muted-foreground">{t("autopilot.none")}</p>
                    ) : (
                      <ul className="space-y-2.5">
                        {factors.active.map((f) => (
                          <li key={f.alpha_id} className="space-y-0.5">
                            <div className="flex items-center justify-between gap-2 text-sm">
                              <span className="font-mono">{f.alpha_id}</span>
                              <span
                                className={cn(
                                  "font-mono text-xs",
                                  (f.screen_ic_mean ?? 0) >= 0
                                    ? "text-success"
                                    : "text-danger",
                                )}
                              >
                                {t("autopilot.factorIc")}{" "}
                                {(f.screen_ic_mean ?? 0) >= 0 ? "+" : ""}
                                {(f.screen_ic_mean ?? 0).toFixed(4)}
                              </span>
                            </div>
                            <FactorBenchRow f={f} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("autopilot.factorPending")}
                    </div>
                    {factors.pending.length === 0 ? (
                      <p className="text-sm text-muted-foreground">{t("autopilot.none")}</p>
                    ) : (
                      <ul className="space-y-1.5">
                        {factors.pending.map((id) => (
                          <li key={id} className="break-all font-mono text-sm">
                            {id}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("autopilot.factorZoo")}
                    </div>
                    {factors.zoo.length === 0 ? (
                      <p className="text-sm text-muted-foreground">{t("autopilot.none")}</p>
                    ) : (
                      <ul className="space-y-1.5">
                        {factors.zoo.map((z) => (
                          <li key={z.alpha_id} className="text-sm">
                            <details className="group">
                              <summary className="cursor-pointer list-none break-all">
                                <span className="font-mono">{z.alpha_id}</span>
                                {z.meta?.nickname ? (
                                  <span className="ml-1.5 text-xs text-muted-foreground">
                                    {z.meta.nickname}
                                  </span>
                                ) : null}
                                {z.meta?.theme?.length ? (
                                  <span className="ml-1.5 text-[10px] text-muted-foreground/70">
                                    {z.meta.theme.join("/")}
                                  </span>
                                ) : null}
                              </summary>
                              {z.meta ? (
                                <div className="mt-1 space-y-1 border-l border-border/60 pl-2 text-[11px] text-muted-foreground">
                                  {z.meta.formula_latex ? (
                                    <div className="break-all font-mono">{z.meta.formula_latex}</div>
                                  ) : null}
                                  <div>
                                    {z.meta.universe?.join(", ")}
                                    {z.meta.frequency?.length ? ` · ${z.meta.frequency.join("/")}` : ""}
                                    {typeof z.meta.decay_horizon === "number"
                                      ? ` · decay ${z.meta.decay_horizon}`
                                      : ""}
                                    {typeof z.meta.min_warmup_bars === "number"
                                      ? ` · warmup ${z.meta.min_warmup_bars}`
                                      : ""}
                                  </div>
                                  {z.meta.notes ? <div className="break-words">{z.meta.notes}</div> : null}
                                </div>
                              ) : null}
                            </details>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("autopilot.factorRetired")}
                    </div>
                    {factors.retired.length === 0 ? (
                      <p className="text-sm text-muted-foreground">{t("autopilot.none")}</p>
                    ) : (
                      <ul className="space-y-1.5">
                        {factors.retired.map((f) => (
                          <li
                            key={f.alpha_id}
                            className="break-all text-sm text-muted-foreground"
                            title={f.reason ?? undefined}
                          >
                            <span className="font-mono">{f.alpha_id}</span>
                            {f.retired_at ? (
                              <span className="ml-1.5 text-xs whitespace-nowrap">
                                {formatTradeTime(f.retired_at, t)}
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">{t("autopilot.factorsEmpty")}</p>
              )}
            </AutopilotPanel>

            <AutopilotPanel title={t("autopilot.perfTitle")} icon={TrendingUp} wide>
              {performance ? (
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <KeyValue
                      label={t("autopilot.perfTotalTrades")}
                      value={String(performance.total_trades)}
                    />
                    <KeyValue
                      label={t("autopilot.perfOpenPositions")}
                      value={String(performance.open_positions)}
                    />
                    <KeyValue
                      label={t("autopilot.perfOpenExposure")}
                      value={`$${performance.open_exposure_usd.toLocaleString("en-US")}`}
                    />
                    <KeyValue
                      label={t("autopilot.perfWinRate")}
                      value={`${(performance.win_rate * 100).toFixed(1)}%`}
                    />
                    <KeyValue
                      label={t("autopilot.perfRealizedPnl")}
                      value={`$${performance.realized_pnl_usd.toLocaleString("en-US")}`}
                      valueTone={
                        performance.realized_pnl_usd > 0
                          ? "success"
                          : performance.realized_pnl_usd < 0
                            ? "danger"
                            : undefined
                      }
                    />
                    <KeyValue
                      label={t("autopilot.perfSharpe")}
                      value={performance.sharpe.toFixed(2)}
                    />
                    <KeyValue
                      label={t("autopilot.perfMaxDrawdown")}
                      value={`${(performance.max_drawdown * 100).toFixed(1)}%`}
                      valueTone={
                        performance.max_drawdown > 0 ? "danger" : undefined
                      }
                    />
                    <KeyValue
                      label={t("autopilot.perfWinLoss")}
                      value={`${performance.wins} / ${performance.losses}`}
                    />
                    {performance.benchmark_return_pct != null && (
                      <KeyValue
                        label={`${t("autopilot.perfBenchmark")} (${performance.benchmark_symbol})`}
                        value={`${performance.benchmark_return_pct > 0 ? "+" : ""}${performance.benchmark_return_pct.toFixed(2)}%`}
                        valueTone={
                          performance.benchmark_return_pct > 0
                            ? "success"
                            : performance.benchmark_return_pct < 0
                              ? "danger"
                              : undefined
                        }
                      />
                    )}
                    {performance.avg_slippage_bps != null && (
                      <KeyValue
                        label={t("autopilot.perfSlippage")}
                        value={`${performance.avg_slippage_bps > 0 ? "+" : ""}${performance.avg_slippage_bps.toFixed(2)} bps`}
                        valueTone={
                          Math.abs(performance.avg_slippage_bps) > 20
                            ? "danger"
                            : undefined
                        }
                      />
                    )}
                  </div>
                  <div className="grid gap-4">
                    <DailyPnlChart days={performance.daily_pnl} />
                    <CumulativePnlChart days={performance.daily_pnl} />
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">{t("autopilot.none")}</p>
              )}
            </AutopilotPanel>

            <AutopilotPanel title={t("autopilot.positionsTitle")} icon={Layers} wide>
              {positions.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("autopilot.positionsEmpty")}</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border/60 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                        <th className="py-2 pr-3 font-medium">{t("autopilot.colSymbol")}</th>
                        <th className="py-2 pr-3 font-medium">{t("autopilot.colSide")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("autopilot.colQty")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("autopilot.colEntryPrice")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("autopilot.colUnrealizedPnl")}</th>
                        <th className="py-2 text-right font-medium">{t("autopilot.colEntryTime")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {positions.map((pos) => (
                        <tr
                          key={pos.symbol}
                          className="border-b border-border/40 last:border-0"
                        >
                          <td className="py-2 pr-3 font-mono">{pos.symbol}</td>
                          <td className="py-2 pr-3 font-medium text-success">
                            {sideLabel(pos.side, t)}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {pos.quantity.toLocaleString("en-US", {
                              maximumFractionDigits: 8,
                            })}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            ${pos.entry_price.toLocaleString("en-US")}
                          </td>
                          <td
                            className={cn(
                              "py-2 pr-3 text-right font-mono",
                              pos.unrealized_pnl > 0 && "text-success",
                              pos.unrealized_pnl < 0 && "text-danger",
                            )}
                          >
                            {pos.unrealized_pnl > 0 ? "+" : ""}
                            ${pos.unrealized_pnl.toLocaleString("en-US")}
                          </td>
                          <td className="py-2 text-right font-mono text-xs">
                            {formatTradeTime(pos.entry_time, t)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </AutopilotPanel>

            <AutopilotPanel title={t("autopilot.tradesTitle")} icon={History} wide>
              {trades.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("autopilot.tradesEmpty")}</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border/60 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                        <th className="py-2 pr-3 font-medium">{t("autopilot.colTime")}</th>
                        <th className="py-2 pr-3 font-medium">{t("autopilot.colEngine")}</th>
                        <th className="py-2 pr-3 font-medium">{t("autopilot.colSymbol")}</th>
                        <th className="py-2 pr-3 font-medium">{t("autopilot.colSide")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("autopilot.colQty")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("autopilot.colPrice")}</th>
                        <th className="py-2 pr-3 text-right font-medium">{t("autopilot.colNotional")}</th>
                        <th className="py-2 text-right font-medium">{t("autopilot.colPnl")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trades.map((trade, index) => (
                        <tr
                          key={`${trade.ts ?? "trade"}-${index}`}
                          className="border-b border-border/40 last:border-0"
                        >
                          <td className="py-2 pr-3 font-mono text-xs">
                            {formatTradeTime(trade.ts, t)}
                          </td>
                          <td className="py-2 pr-3">{engineLabel(trade.engine, t)}</td>
                          <td className="py-2 pr-3 font-mono">{trade.symbol}</td>
                          <td
                            className={cn(
                              "py-2 pr-3 font-medium",
                              trade.side === "buy" ? "text-success" : "text-danger",
                            )}
                          >
                            {sideLabel(trade.side, t)}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {trade.quantity ?? "-"}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            {trade.price != null ? `$${trade.price}` : "-"}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">
                            ${trade.notional.toLocaleString("en-US")}
                          </td>
                          <td
                            className={cn(
                              "py-2 text-right font-mono",
                              (trade.realized_pnl ?? 0) > 0 && "text-success",
                              (trade.realized_pnl ?? 0) < 0 && "text-danger",
                            )}
                          >
                            {trade.realized_pnl != null
                              ? `$${trade.realized_pnl.toLocaleString("en-US")}`
                              : "-"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </AutopilotPanel>
          </>
        ) : null}
      </div>
    </div>
  );
}

interface SummaryTileProps {
  label: string;
  value: string;
  tone: "success" | "danger" | "warning" | "neutral";
  icon: typeof Activity;
}

function isCurrentRequest(
  activeRequest: { id: number; controller: AbortController } | null,
  requestId: number,
  controller: AbortController,
): boolean {
  return activeRequest?.id === requestId && activeRequest.controller === controller;
}

function SummaryTile({ label, value, tone, icon: Icon }: SummaryTileProps) {
  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
        <Icon
          className={cn(
            "h-4 w-4",
            tone === "success" && "text-success",
            tone === "danger" && "text-danger",
            tone === "warning" && "text-warning",
            tone === "neutral" && "text-muted-foreground",
          )}
        />
      </div>
      <div
        className={cn(
          "mt-3 text-2xl font-semibold capitalize",
          tone === "success" && "text-success",
          tone === "danger" && "text-danger",
          tone === "warning" && "text-warning",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function AutopilotPanel({
  title,
  icon: Icon,
  children,
  wide = false,
}: {
  title: string;
  icon: typeof Activity;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <section
      className={cn(
        "rounded-xl border border-border/60 bg-card p-4 shadow-sm",
        wide && "lg:col-span-2",
      )}
    >
      <div className="mb-3 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {title}
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function KeyValue({
  label,
  value,
  valueTone,
}: {
  label: string;
  value: string;
  valueTone?: "success" | "danger";
}) {
  return (
    <div>
      <div className="text-[11px] uppercase text-muted-foreground">{label}</div>
      <div
        className={cn(
          "font-mono text-sm",
          valueTone === "success" && "text-success",
          valueTone === "danger" && "text-danger",
        )}
      >
        {value || "-"}
      </div>
    </div>
  );
}

export function DailyPnlChart({ days }: { days: AutopilotDailyPnl[] }) {
  const { t } = useTranslation();
  if (days.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("autopilot.perfDailyEmpty")}</p>;
  }
  const maxAbs = Math.max(...days.map((d) => Math.abs(d.pnl_usd)), 1);
  return (
    <div>
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t("autopilot.perfDailyTitle")}
      </div>
      <div className="flex h-20 items-end gap-1.5">
        {days.map((d) => {
          const height = Math.max(4, (Math.abs(d.pnl_usd) / maxAbs) * 100);
          return (
            <div
              key={d.date}
              className="group relative flex h-full flex-1 flex-col justify-end"
              title={`${d.date}: $${d.pnl_usd.toFixed(2)}`}
            >
              <div
                className={cn(
                  "w-full rounded-sm transition-all",
                  d.pnl_usd >= 0 ? "bg-success/80" : "bg-danger/80",
                )}
                style={{ height: `${height}%` }}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FactorBenchRow({
  f,
}: {
  f: { ic_mean: number | null; alpha_t_full: number | null; category: string | null };
}) {
  const bits: string[] = [];
  if (f.ic_mean != null) bits.push(`IC ${f.ic_mean.toFixed(4)}`);
  if (f.alpha_t_full != null) bits.push(`α_t ${f.alpha_t_full.toFixed(2)}`);
  if (f.category) bits.push(f.category);
  if (bits.length === 0) return null;
  return (
    <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
      {bits.map((bit) => (
        <span key={bit} className="font-mono">
          {bit}
        </span>
      ))}
    </div>
  );
}

export function CumulativePnlChart({ days }: { days: AutopilotDailyPnl[] }) {
  const { t } = useTranslation();
  if (days.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("autopilot.perfDailyEmpty")}</p>;
  }
  const cumulative = days.map((d, i) => {
    const running = days
      .slice(0, i + 1)
      .reduce((acc, day) => acc + day.pnl_usd, 0);
    return { date: d.date, pnl_usd: running };
  });
  const values = cumulative.map((d) => d.pnl_usd);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const span = Math.max(max - min, 1);
  const width = 100;
  const height = 100;
  const points = cumulative
    .map((d, i) => {
      const x = (i / Math.max(cumulative.length - 1, 1)) * width;
      const y = height - ((d.pnl_usd - min) / span) * height;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  const last = cumulative[cumulative.length - 1];
  const up = last.pnl_usd >= 0;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <span>{t("autopilot.perfCumulativeTitle")}</span>
        <span className={cn("font-mono", up ? "text-success" : "text-danger")}>
          {up ? "+" : ""}
          ${last.pnl_usd.toFixed(2)}
        </span>
      </div>
      <div className="h-20 w-full">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full">
          <polyline
            points={points}
            fill="none"
            stroke={up ? "hsl(var(--success))" : "hsl(var(--danger))"}
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
          />
          <circle
            cx={100}
            cy={100 - ((last.pnl_usd - min) / span) * 100}
            r="1.6"
            fill={up ? "hsl(var(--success))" : "hsl(var(--danger))"}
          />
        </svg>
      </div>
    </div>
  );
}

function formatTimestamp(
  iso: string | null | undefined,
  t: TFunction,
  nowMs: number,
): string {
  if (!iso) return t("autopilot.never");
  const timestamp = new Date(iso).getTime();
  if (!Number.isFinite(timestamp)) return t("autopilot.never");
  const deltaSec = Math.round((nowMs - timestamp) / 1000);
  const ago = formatAgo(deltaSec, t);
  return `${new Date(iso).toLocaleString()} (${ago})`;
}

function formatHeartbeatAge(
  heartbeatMs: number | null | undefined,
  t: TFunction,
  nowMs: number,
): string {
  if (heartbeatMs == null || !Number.isFinite(heartbeatMs)) return t("autopilot.never");
  const deltaSec = Math.round((nowMs - heartbeatMs) / 1000);
  if (deltaSec < 0) return "0s";
  return formatAgo(deltaSec, t);
}

function formatTradeTime(iso: string | null | undefined, t: TFunction): string {
  if (!iso) return t("autopilot.never");
  const timestamp = new Date(iso).getTime();
  if (!Number.isFinite(timestamp)) return t("autopilot.never");
  return new Date(iso).toLocaleString();
}

function sideLabel(side: string, t: TFunction): string {
  if (side === "buy") return t("autopilot.buy");
  if (side === "sell") return t("autopilot.sell");
  return side;
}

function engineLabel(engine: string, t: TFunction): string {
  if (engine === "paper") return t("autopilot.enginePaper");
  if (engine === "live") return t("autopilot.engineLive");
  return engine;
}

function formatAgo(deltaSec: number, t: TFunction): string {
  if (deltaSec < 60) return `${Math.max(0, deltaSec)}s ${t("autopilot.ago")}`;
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ${t("autopilot.ago")}`;
  return `${Math.floor(deltaSec / 3600)}h ${t("autopilot.ago")}`;
}
