"""Main 24/7 orchestrator for the crypto autopilot pipeline.

Ties together all phases: collect → mine → evaluate → paper_trade → live → feedback.
Uses a simple asyncio loop with ``asyncio.sleep()`` intervals for tick scheduling,
with crash recovery via :class:`HealthMonitor` pipeline state.

Each tick method is wrapped in try/except — a single tick failure never crashes
the loop. Errors are logged and the loop continues.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.crypto_autopilot.auto_backtest import AutoBacktester
from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.crypto_autopilot.factor_dedup import (
    compute_ic_series,
    dedup_rejection_reason,
)
from src.crypto_autopilot.factor_miner import FactorMiner
from src.crypto_autopilot.factor_screen import FactorScreen
from src.crypto_autopilot.factor_store import FactorStore
from src.crypto_autopilot.feedback import FeedbackAnalyzer
from src.crypto_autopilot.gap_report import build_gap_report
from src.crypto_autopilot.health import HealthMonitor
from src.crypto_autopilot.history_store import HistoryStore
from src.crypto_autopilot.live_scale import current_live_scale, maybe_scale_up
from src.crypto_autopilot.llm_budget import LLMBudget
from src.crypto_autopilot.live_executor import LiveExecutor
from src.crypto_autopilot.market_feed import MarketFeed
from src.crypto_autopilot.market_regime import classify_regime
from src.crypto_autopilot.memory_guard import MemoryGuard
from src.crypto_autopilot.notifier import AutopilotNotifier
from src.crypto_autopilot.overfit_gate import OverfitGate
from src.crypto_autopilot.panel_builder import PanelBuilder
from src.crypto_autopilot.paper_engine import PaperEngine
from src.crypto_autopilot.paper_monitor import PaperMonitor
from src.crypto_autopilot.promotion import PromotionGate
from src.crypto_autopilot.risk_monitor import RiskMonitor
from src.crypto_autopilot.types import (
    FactorCandidate,
    FactorLifecycle,
    PipelinePhase,
    PipelineState,
)

logger = logging.getLogger(__name__)

#: Heartbeat write interval for the dedicated liveness task (seconds). Kept
#: well below the stale threshold (300s) so a slow tick round never makes a
#: healthy loop look dead.
_HEARTBEAT_INTERVAL_S: int = 30

__all__ = ["AutopilotOrchestrator"]


def _now_ms() -> int:
    """Return the current wall-clock time in epoch milliseconds."""
    return int(time.time() * 1000)


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def _meta_value(candidate: Any, key: str) -> Any:
    """Best-effort read of a candidate's meta dict (None-safe)."""
    meta = getattr(candidate, "meta", None)
    if not isinstance(meta, dict):
        return None
    return meta.get(key)


#: Backtest report metric keys surfaced in the factor observability snapshot.
_BENCH_METRIC_KEYS: tuple[str, ...] = (
    "ic_mean", "ir", "alpha_t_full", "alpha_t_train", "alpha_t_test",
    "category",
)


def _bench_metrics(report: Any) -> dict[str, Any]:
    """Extract known bench metrics from a backtest report (None-safe)."""
    metrics = getattr(report, "metrics", None) or {}
    if not isinstance(metrics, dict):
        return {}
    return {k: metrics[k] for k in _BENCH_METRIC_KEYS if k in metrics}


def _default_runtime_root() -> Path:
    """Return the default runtime root for health/heartbeat files.

    Returns:
        ``<agent>/runs/autopilot/``
    """
    return Path(__file__).resolve().parents[2] / "runs" / "autopilot"


class AutopilotOrchestrator:
    """24/7 orchestrator for the crypto autopilot pipeline.

    Ties together all phases: collect → mine → evaluate → paper_trade → live → feedback.
    The orchestrator runs a simple asyncio loop with configurable intervals for each
    phase. Pipeline state is persisted via :class:`HealthMonitor` for crash recovery.

    Attributes:
        config: Autopilot tuning knobs.
        health: Heartbeat writer and pipeline-state store.
        pipeline_state: Current pipeline phase and counters.
    """

    def __init__(
        self,
        config: AutopilotConfig | None = None,
        decay_manager: Any | None = None,
    ) -> None:
        """Initialize all components and load pipeline state from HealthMonitor.

        Args:
            config: Autopilot config; loaded from env when ``None``.
            decay_manager: Optional
                :class:`~src.crypto_autopilot.decay_integration.AutopilotDecayManager`
                that closes the SDM decay loop (auto-retire of decaying
                factors).  When ``None``, decay scanning is disabled.
        """
        self.config: AutopilotConfig = config or load_autopilot_config()

        # Health monitor for heartbeat + pipeline-state durability.
        runtime_root = _default_runtime_root()
        self.health: HealthMonitor = HealthMonitor(runtime_root)
        # Load persisted pipeline state (crash recovery).
        saved_state = self.health.load_pipeline_state()
        self.pipeline_state: PipelineState = saved_state or PipelineState()

        # Core components.
        self._budget: LLMBudget = LLMBudget()
        self._feed: MarketFeed = MarketFeed(autopilot_config=self.config)
        self._panel_builder: PanelBuilder = PanelBuilder()
        self._memory_guard: MemoryGuard = MemoryGuard()
        self._factor_miner: FactorMiner = FactorMiner(
            model_name=self.config.deepseek_model,
            budget=self._budget,
        )
        self._factor_screen: FactorScreen = FactorScreen()
        self._factor_store: FactorStore = FactorStore()
        self._decay_manager: Any | None = decay_manager
        self._notifier: AutopilotNotifier = AutopilotNotifier(runtime_root)
        self._backtester: AutoBacktester = AutoBacktester(
            bars_per_year=self.config.bars_per_year,
        )
        self._overfit_gate: OverfitGate = OverfitGate()
        self._paper_engine: PaperEngine = PaperEngine(config=self.config)
        self._paper_monitor: PaperMonitor = PaperMonitor(
            self._paper_engine, config=self.config,
        )
        self._promotion_gate: PromotionGate = PromotionGate(config=self.config)
        # Phase 4: shadow-capable live executor — mirrors every live fill
        # with a same-signal paper fill while ``live_shadow_enabled`` so the
        # paper-vs-live gap report can compare execution quality.
        self._live_executor: LiveExecutor = LiveExecutor(
            config=self.config,
            runtime_root=runtime_root,
            paper_engine=self._paper_engine,
            shadow_mode=self.config.live_shadow_enabled,
        )
        self._risk_monitor: RiskMonitor = RiskMonitor(config=self.config)
        self._feedback: FeedbackAnalyzer = FeedbackAnalyzer(config=self.config)
        # Long-window parquet history backing statistical evaluation.
        self._history: HistoryStore = HistoryStore()
        # Data-freshness alert latch: True while a stale-symbol alert is pending.
        self._data_stale_notified: bool = False

        # In-memory working set.
        self._panel: dict[str, Any] = {}
        self._pending_candidates: list[FactorCandidate] = []
        self._active_factors: list[dict[str, Any]] = []
        # Latest market-regime classification (computed each feedback tick,
        # surfaced in state.json and the feedback prompt).
        self._regime_snapshot: dict[str, Any] = {}
        # Retirement audit trail: every factor that fails the promotion gate
        # or decays past the disabled threshold is recorded here and surfaced
        # in the factors.json observability snapshot.
        self._retired_factors: list[dict[str, Any]] = []
        self._mining_hints: list[str] = []
        # Monotonic timestamp of the last placed paper order (cooldown gate).
        self._last_trade_ts: float = 0.0

        # Loop control.
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    def attach_decay_manager(self, decay_manager: Any | None) -> None:
        """Attach the SDM decay manager, binding it to the pipeline store.

        Re-binds the manager to this orchestrator's :class:`FactorStore` so
        decay-triggered retirements flow through the same hypotheses
        registry the pipeline uses, and wires the SDM strategy store back
        into that store for artifact registration + bench recording.

        Args:
            decay_manager: An
                :class:`~src.crypto_autopilot.decay_integration.AutopilotDecayManager`
                instance, or ``None`` to disable decay scanning.
        """
        self._decay_manager = decay_manager
        if decay_manager is None:
            return
        decay_manager.factor_store = self._factor_store
        store = getattr(decay_manager, "_strategy_store", None)
        if store is not None and self._factor_store.strategy_store is None:
            self._factor_store.strategy_store = store

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Set up asyncio tick loop and run until stopped.

        Intervals from config:
        - mine every ``mine_interval_hours``
        - evaluate every ``evaluate_interval_hours``
        - trade every ``trade_interval_minutes``
        - feedback every ``feedback_interval_hours``

        Writes heartbeat each tick. The loop runs until :meth:`stop` is called
        or a KeyboardInterrupt is received.
        """
        if self._running:
            logger.warning("orchestrator already running")
            return

        self._running = True
        logger.info(
            "autopilot orchestrator starting (phase=%s, tick_count=%d)",
            self.pipeline_state.phase.value,
            self.pipeline_state.tick_count,
        )

        mine_interval_s = max(1, self.config.mine_interval_hours) * 3600
        evaluate_interval_s = max(1, self.config.evaluate_interval_hours) * 3600
        trade_interval_s = max(1, self.config.trade_interval_minutes) * 60
        feedback_interval_s = max(1, self.config.feedback_interval_hours) * 3600

        # Heartbeat cadence decoupled from the tick loop: a slow tick round
        # (multi-pair collect + LLM mining) plus the interval sleep can stretch
        # heartbeat gaps past the stale threshold (300s), making a healthy
        # loop look dead. A dedicated task keeps the liveness signal fresh.
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("heartbeat task started")

        # Track last-run timestamps for each phase.
        last_mine = 0.0
        last_evaluate = 0.0
        last_trade = 0.0
        last_feedback = 0.0

        # Short collect interval — runs every trade tick.
        collect_interval_s = trade_interval_s

        while self._running:
            now = time.monotonic()

            # Collect + trade phases — same cadence, keep data fresh before
            # trading.  Collect refreshes the panel, then trade can read
            # current prices/factors immediately after.
            if now - last_trade >= collect_interval_s:
                await self._safe_tick("collect", self._tick_collect)
                await self._safe_tick("trade", self._tick_trade)
                last_trade = now

            # Mine phase.
            if now - last_mine >= mine_interval_s:
                await self._safe_tick("mine", self._tick_mine)
                last_mine = now

            # Evaluate phase.
            if now - last_evaluate >= evaluate_interval_s:
                await self._safe_tick("evaluate", self._tick_evaluate)
                last_evaluate = now

            # Feedback phase.
            if now - last_feedback >= feedback_interval_s:
                await self._safe_tick("feedback", self._tick_feedback)
                last_feedback = now

            # Persist pipeline state.
            self._save_state()

            # Memory guard.
            try:
                self._memory_guard.maybe_gc(self.pipeline_state.tick_count)
            except Exception:  # noqa: BLE001
                pass

            # Sleep until next trade tick (shortest interval).
            await asyncio.sleep(trade_interval_s)

    async def stop(self) -> None:
        """Graceful shutdown — save pipeline state and stop the loop."""
        logger.info("autopilot orchestrator stopping")
        self._running = False
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._save_state()
        self._task = None
        logger.info("autopilot orchestrator stopped")

    # ------------------------------------------------------------------
    # Tick methods — each wrapped in try/except by _safe_tick
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Write a fresh heartbeat every 30s regardless of tick cadence.

        Best-effort: a failed write is logged and swallowed so a
        liveness-signal problem can never block trading.
        """
        while self._running:
            try:
                self.health.write_heartbeat(_now_ms())
            except Exception as exc:  # noqa: BLE001
                logger.debug("heartbeat write failed: %s", exc)
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)

    async def _tick_collect(self) -> None:
        """Fetch K-line data, build panel, trim with MemoryGuard."""
        self._set_phase(PipelinePhase.COLLECTING)
        logger.debug("tick_collect: fetching bars for %s", self.config.pairs)

        bars = self._feed.fetch_panel(
            pairs=self.config.pairs,
            period=self.config.bar_period,
            limit=self.config.bar_limit,
        )
        if not bars:
            logger.warning("tick_collect: no bars fetched")
            return

        panel = self._panel_builder.build_panel(bars)
        if not panel:
            logger.warning("tick_collect: empty panel after build")
            return

        # Trim to sliding window.
        self._panel = self._memory_guard.trim_window(
            panel, self._memory_guard.max_history_bars,
        )
        logger.info(
            "tick_collect: panel built with %d fields, %s",
            len(self._panel),
            {k: getattr(v, "shape", "?") for k, v in self._panel.items()},
        )

        # Persist data-freshness health and incrementally extend the history
        # store so long-window evaluation stays current (best-effort — data
        # lag must never block trading).
        self._record_data_health()
        try:
            self._history.append_latest(
                self.config.pairs, period=self.config.bar_period,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tick_collect: history append failed: %s", exc)

    def _bar_period_hours(self) -> float:
        """Return the configured bar period in hours (1h→1, 4h→4, 1d→24)."""
        period = self.config.bar_period.lower()
        if period.endswith("h"):
            return max(1.0, float(period[:-1]))
        if period.endswith("d"):
            return float(period[:-1]) * 24.0
        if period.endswith("m"):
            return max(1.0, float(period[:-1])) / 60.0
        return 1.0

    def _record_data_health(self) -> None:
        """Persist per-symbol data freshness and alert once when symbols lag.

        Writes ``data_health.json`` next to the factors snapshot and enqueues
        a ``data_stale`` notification on the stale→fresh transition (one-shot
        per episode, so a single outage does not spam the outbox). Best-effort:
        a failed write or notification never breaks the trading loop.
        """
        import json

        import pandas as pd

        symbols: dict[str, Any] = {}
        stale: list[str] = []
        max_lag_h = self._bar_period_hours() * 2.0
        now = pd.Timestamp.utcnow().tz_localize(None)
        for symbol in self.config.pairs:
            latest = self._history.latest_ts(
                symbol, period=self.config.bar_period,
            )
            if latest is None:
                stale.append(symbol)
                symbols[symbol] = {"latest_ts": None, "lag_hours": None}
                continue
            lag_h = (now - latest).total_seconds() / 3600.0
            symbols[symbol] = {
                "latest_ts": latest.isoformat(),
                "lag_hours": round(lag_h, 2),
            }
            if lag_h > max_lag_h:
                stale.append(symbol)
        payload = {
            "updated_at": _utc_now().isoformat(),
            "stale_symbols": stale,
            "symbols": symbols,
        }
        try:
            path = Path(_default_runtime_root()) / "data_health.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("data_health write failed: %s", exc)

        if stale and not self._data_stale_notified:
            self._notifier.notify(
                "data_stale",
                "Market data stale",
                f"Symbols behind by more than 2 bars: {', '.join(stale)}.",
                meta={"stale_symbols": stale},
            )
            self._data_stale_notified = True
            logger.warning("tick_collect: stale data for %s", stale)
        elif not stale and self._data_stale_notified:
            self._data_stale_notified = False
            logger.info("tick_collect: data freshness restored")

    async def _tick_mine(self) -> None:
        """Call FactorMiner.mine_factors(), quick screen, store passing candidates."""
        self._set_phase(PipelinePhase.DISCOVERING)

        if not self._panel or "close" not in self._panel:
            logger.info("tick_mine: no panel available; skipping")
            return

        candidates = self._factor_miner.mine_factors(
            panel=self._panel,
            n_candidates=3,
            theme_hints=self._mining_hints or None,
        )
        if not candidates:
            logger.info("tick_mine: no candidates mined")
            return

        # Quick screen each candidate.
        close = self._panel.get("close")
        if close is None:
            return

        return_df = FactorScreen.compute_returns(self._panel)
        passing: list[FactorCandidate] = []

        for candidate in candidates:
            try:
                # Execute the factor's compute function to get factor values.
                factor_df = self._execute_factor(candidate)
                if factor_df is None:
                    continue
                metrics = self._factor_screen.screen(factor_df, return_df)
                if metrics.get("pass_screen"):
                    # Remember the screen IC direction so the trade tick
                    # can gate orders on the factor's signed signal.
                    candidate.meta["screen_ic_mean"] = metrics.get("ic_mean", 0.0)
                    passing.append(candidate)
                    logger.info(
                        "tick_mine: %s passed screen (ic_mean=%.4f)",
                        candidate.alpha_id,
                        metrics.get("ic_mean", 0),
                    )
                else:
                    logger.info(
                        "tick_mine: %s failed screen (ic_mean=%.4f)",
                        candidate.alpha_id,
                        metrics.get("ic_mean", 0),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("tick_mine: screen error for %s: %s", candidate.alpha_id, exc)

        # Store passing candidates.
        for candidate in passing:
            try:
                self._factor_store.store(candidate)
                self._pending_candidates.append(candidate)
                logger.info("tick_mine: stored %s", candidate.alpha_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("tick_mine: store error for %s: %s", candidate.alpha_id, exc)

        logger.info(
            "tick_mine: %d/%d candidates passed screen",
            len(passing), len(candidates),
        )

    async def _tick_evaluate(self) -> None:
        """For each untested factor: backtest, overfit gate, promote or retire."""
        self._set_phase(PipelinePhase.BACKTESTING)

        if not self._pending_candidates:
            logger.debug("tick_evaluate: no pending candidates")
            return

        # Prefer a long evaluation window from the history store: statistical
        # gates are meaningless on the ~7.5-day live panel, so fall back only
        # when the store has no data yet.
        panel = self._panel
        try:
            hist_panel = self._history.get_panel(
                self.config.pairs,
                period=self.config.bar_period,
                bars=self.config.eval_bars,
            )
            if hist_panel and "close" in hist_panel:
                panel = hist_panel
                logger.info(
                    "tick_evaluate: eval window %d bars from history store",
                    self.config.eval_bars,
                )
            else:
                logger.warning(
                    "tick_evaluate: history store empty; falling back to live panel",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "tick_evaluate: history panel failed (%s); using live panel", exc,
            )

        if not panel or "close" not in panel:
            logger.info("tick_evaluate: no panel available; skipping")
            return

        remaining: list[FactorCandidate] = []
        for candidate in self._pending_candidates:
            try:
                report = self._backtester.run_backtest_for_factor(candidate, panel)
                passes, reason, details = self._overfit_gate.evaluate(candidate, report)

                if passes:
                    # Phase 3: factor dedup — reject candidates whose IC
                    # series is too correlated with an already-active factor.
                    dedup_rejected, dedup_reason = self._factor_dedup_check(
                        candidate, panel,
                    )
                    if dedup_rejected:
                        self._factor_store.advance_lifecycle(
                            candidate.alpha_id, FactorLifecycle.RETIRED,
                        )
                        self._retired_factors.append({
                            "alpha_id": candidate.alpha_id,
                            "retired_at": _utc_now().isoformat(),
                            "reason": dedup_reason,
                        })
                        logger.info(
                            "tick_evaluate: %s rejected — %s",
                            candidate.alpha_id, dedup_reason,
                        )
                        continue

                    # Advance to paper trading.
                    self._factor_store.advance_lifecycle(
                        candidate.alpha_id, FactorLifecycle.BACKTESTED,
                    )
                    # Feed the SDM decay monitor with the backtest metrics.
                    try:
                        report_metrics = report.metrics if hasattr(report, "metrics") else None
                        self._factor_store.record_bench(
                            candidate.alpha_id, report_metrics or {},
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "tick_evaluate: bench record failed for %s: %s",
                            candidate.alpha_id, exc,
                        )
                    self._active_factors.append({
                        "alpha_id": candidate.alpha_id,
                        "lifecycle": FactorLifecycle.BACKTESTED.value,
                        "report": report,
                        "candidate": candidate,
                    })
                    logger.info(
                        "tick_evaluate: %s passed all gates → paper trading",
                        candidate.alpha_id,
                    )
                else:
                    # Retire the factor.
                    self._factor_store.advance_lifecycle(
                        candidate.alpha_id, FactorLifecycle.RETIRED,
                    )
                    self._retired_factors.append({
                        "alpha_id": candidate.alpha_id,
                        "retired_at": _utc_now().isoformat(),
                        "reason": reason,
                    })
                    logger.info(
                        "tick_evaluate: %s retired — %s",
                        candidate.alpha_id, reason,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "tick_evaluate: error for %s: %s",
                    candidate.alpha_id, exc,
                )
                # Keep it for retry on next evaluate tick.
                remaining.append(candidate)

        self._pending_candidates = remaining

    async def _tick_trade(self) -> None:
        """Check risk, get factor signals, place orders via PaperEngine or LiveExecutor."""
        self._set_phase(PipelinePhase.PAPER_TRADING)

        # Check risk monitor.
        if self._risk_monitor.is_halted():
            logger.warning("tick_trade: trading halted by risk monitor")
            return

        # Phase 4: staged live scale-up — cheap file read, no-op on most
        # ticks; a tier advance is notified to operators.
        try:
            scale_result = maybe_scale_up(
                self.health.runtime_root,
                self.config,
                halt_active=self._risk_monitor.is_halted(),
            )
            if scale_result.get("scaled_up"):
                self._notifier.notify(
                    "live_scale_up",
                    "Live order scale raised to "
                    f"${scale_result['scale']:.0f}",
                    str(scale_result.get("reason", "")),
                    meta={
                        "old_scale": scale_result.get("old_scale"),
                        "new_scale": scale_result.get("scale"),
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tick_trade: live scale-up check failed: %s", exc)

        # Manage open positions first — take-profit / stop-loss / max
        # holding. Position exits are independent of factor signals and are
        # never blocked by the buy-side cooldown or daily quota.
        self._manage_positions()

        if not self._active_factors:
            logger.debug("tick_trade: no active factors to trade")
            return

        # For each active factor in paper-trading phase, generate a simple signal
        # and place a small notional order via PaperEngine. Orders are gated by
        # a global cooldown and by the factor's live signal, so a flat market
        # (or a factor whose latest value is below its IC direction) does not
        # fire unconditional buys every loop.
        now_ts = time.monotonic()
        cooldown_s = self.config.trade_cooldown_minutes * 60
        # Phase 3: per-factor order sizing — weight each factor's notional
        # share by |screen IC| (equal weight when > 3 factors).
        factor_weights = self._factor_weight_map()
        for factor_info in self._active_factors:
            try:
                alpha_id = factor_info["alpha_id"]
                lifecycle = factor_info.get("lifecycle", "")

                if lifecycle == FactorLifecycle.BACKTESTED.value:
                    # Paper-trade this factor.
                    self._set_phase(PipelinePhase.PAPER_TRADING)
                    if now_ts - self._last_trade_ts < cooldown_s:
                        logger.info(
                            "tick_trade: cooldown active — skip %s "
                            "(%.0fm until next order)",
                            alpha_id,
                            (cooldown_s - (now_ts - self._last_trade_ts)) / 60,
                        )
                        continue
                    if not self._factor_has_signal(factor_info):
                        logger.info(
                            "tick_trade: %s has no signal this tick — skip",
                            alpha_id,
                        )
                        continue
                    notional = min(
                        self.config.max_order_notional_usd,
                        self.config.max_total_exposure_usd / max(len(self._active_factors), 1),
                    )
                    notional *= factor_weights.get(alpha_id, 1.0)
                    # Simple long signal for the first configured pair.
                    if self.config.pairs:
                        result = self._paper_engine.place_order(
                            symbol=self.config.pairs[0],
                            side="buy",
                            notional=notional,
                            alpha_id=alpha_id,
                        )
                        if result.get("status") == "ok":
                            self._last_trade_ts = time.monotonic()
                        logger.info(
                            "tick_trade: paper order for %s via %s: %s",
                            self.config.pairs[0], alpha_id, result.get("status"),
                        )

                elif lifecycle == FactorLifecycle.PAPER_VALIDATED.value:
                    # Check promotion.
                    promoted, reason, details = self._promotion_gate.evaluate(
                        self._paper_monitor,
                    )
                    if promoted:
                        # Phase 3: out-of-sample recheck on the long history
                        # window before promoting — a paper run can look good
                        # while the factor overfits the paper period.
                        oos_ok, oos_details = self._promotion_oos_recheck(
                            factor_info,
                        )
                        if not oos_ok:
                            factor_info["oos_recheck"] = oos_details
                            logger.info(
                                "tick_trade: %s gate-passed but OOS recheck "
                                "failed — keep paper (%s)",
                                alpha_id, oos_details.get("reason", "unknown"),
                            )
                            self._notifier.notify(
                                "factor_oos_recheck",
                                f"Factor held in paper: {alpha_id}",
                                "OOS recheck failed — "
                                + str(oos_details.get("reason", "unknown")),
                                meta={
                                    "alpha_id": alpha_id,
                                    "stage": "paper_validated",
                                    "reason": oos_details.get("reason", ""),
                                },
                            )
                        else:
                            self._factor_store.advance_lifecycle(
                                alpha_id, FactorLifecycle.LIVE_DEPLOYED,
                            )
                            factor_info["lifecycle"] = FactorLifecycle.LIVE_DEPLOYED.value
                            self._notifier.notify(
                                "factor_promoted",
                                f"Factor promoted: {alpha_id}",
                                "paper → live",
                                meta={"alpha_id": alpha_id, "stage": "live_deployed"},
                            )
                            logger.info("tick_trade: %s promoted to live", alpha_id)
                    else:
                        verdict = self._promotion_gate.decide_retire_or_retry(
                            self._paper_monitor,
                        )
                        if verdict == "retire":
                            self._factor_store.advance_lifecycle(
                                alpha_id, FactorLifecycle.RETIRED,
                            )
                            self._notifier.notify(
                                "factor_retired",
                                f"Factor retired: {alpha_id}",
                                reason or "promotion gate retire",
                                meta={
                                    "alpha_id": alpha_id,
                                    "reason": reason or "promotion gate retire",
                                },
                            )
                            logger.info("tick_trade: %s retired — %s", alpha_id, reason)

                elif lifecycle == FactorLifecycle.LIVE_DEPLOYED.value:
                    # Phase 4: live order path — sized by the staged
                    # scale ladder (initial $5, max $50). Shadow mode
                    # mirrors the fill into paper for the gap report.
                    if now_ts - self._last_trade_ts < cooldown_s:
                        logger.info(
                            "tick_trade: cooldown active — skip live %s "
                            "(%.0fm until next order)",
                            alpha_id,
                            (cooldown_s - (now_ts - self._last_trade_ts)) / 60,
                        )
                        continue
                    if not self._factor_has_signal(factor_info):
                        logger.info(
                            "tick_trade: live %s has no signal this tick — skip",
                            alpha_id,
                        )
                        continue
                    live_scale = current_live_scale(
                        self.health.runtime_root,
                        initial=self.config.live_order_scale,
                    )
                    live_notional = min(
                        live_scale,
                        self.config.max_total_exposure_usd
                        / max(len(self._active_factors), 1),
                    )
                    live_notional *= factor_weights.get(alpha_id, 1.0)
                    if self.config.pairs:
                        result = self._live_executor.place_order(
                            symbol=self.config.pairs[0],
                            side="buy",
                            notional=live_notional,
                        )
                        if result.get("status") == "ok":
                            self._last_trade_ts = time.monotonic()
                        logger.info(
                            "tick_trade: live order for %s via %s: %s "
                            "(scale $%.0f)",
                            self.config.pairs[0], alpha_id,
                            result.get("status"), live_scale,
                        )

            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "tick_trade: error for %s: %s",
                    factor_info.get("alpha_id", "?"), exc,
                )

    def _manage_positions(self) -> None:
        """Close open positions on take-profit, stop-loss, or max holding.

        Runs every trade tick so risk is trimmed even when no factor fires
        a signal. Each position is checked against the configured profit/
        loss targets and the maximum holding window; the first trigger
        closes the position at market.
        """
        try:
            positions = self._paper_engine.get_positions()
        except Exception as exc:  # noqa: BLE001
            logger.warning("tick_trade: cannot list positions: %s", exc)
            return
        for pos in positions:
            try:
                reason = None
                if pos.unrealized_pnl >= self.config.take_profit_usd:
                    reason = "take_profit"
                elif pos.unrealized_pnl <= self.config.stop_loss_usd:
                    reason = "stop_loss"
                else:
                    held_hours = (
                        time.time() - pos.entry_time.timestamp()
                    ) / 3600.0
                    if held_hours >= self.config.max_holding_hours:
                        reason = "max_holding"
                if reason is None:
                    continue
                result = self._paper_engine.close_position(pos.symbol)
                logger.info(
                    "tick_trade: %s closed (%s, pnl=%.2f): %s",
                    pos.symbol, reason, pos.unrealized_pnl,
                    result.get("status"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "tick_trade: close %s failed: %s", pos.symbol, exc,
                )

    def _factor_has_signal(self, factor_info: dict[str, Any]) -> bool:
        """Return whether an active factor's latest value fires a long signal.

        The factor's cross-sectional value at the most recent bar is
        projected onto the direction of its screen IC (factors screened
        with negative IC are traded inversely). A flat/NaN panel or a
        missing factor module yields ``False`` (no order).

        Args:
            factor_info: An entry from ``self._active_factors``.

        Returns:
            ``True`` when the signed latest value is positive.
        """
        candidate = factor_info.get("candidate")
        if candidate is None:
            return False
        direction = 1.0
        ic_mean = candidate.meta.get("screen_ic_mean")
        if ic_mean is not None:
            direction = 1.0 if float(ic_mean) >= 0 else -1.0
        factor_df = self._execute_factor(candidate)
        if factor_df is None or factor_df.empty:
            return False
        latest = factor_df.iloc[-1]
        try:
            values = latest.astype(float)
        except (TypeError, ValueError):
            return False
        if values.isna().all():
            return False
        return float(values.mean()) * direction > 0.0

    async def _tick_feedback(self) -> None:
        """Call FeedbackAnalyzer.analyze(), update mining hints."""
        self._set_phase(PipelinePhase.FEEDBACK)

        # Phase 3: refresh the market-regime snapshot from the local panel
        # and attach it to the factor results so the prompt can reason about
        # factor behaviour conditional on the regime.
        self._regime_snapshot = self._current_regime()

        # Run the SDM decay scan first — factors whose IC/Sharpe decayed
        # past the disabled threshold are retired + notified here.
        if self._decay_manager is not None:
            try:
                summary = self._decay_manager.run_scan()
                if summary.get("retired"):
                    logger.warning(
                        "tick_feedback: decay scan retired %d factor(s)",
                        len(summary["retired"]),
                    )
                    for entry in summary["retired"]:
                        self._retired_factors.append({
                            "alpha_id": entry.get("artifact_id", ""),
                            "retired_at": _utc_now().isoformat(),
                            "reason": "decay scan: %s signal" % entry.get("signal", ""),
                        })
            except Exception as exc:  # noqa: BLE001
                logger.warning("tick_feedback: decay scan failed: %s", exc)

        # Build factor results from active factors.
        factor_results: list[dict[str, Any]] = []
        for factor_info in self._active_factors:
            alpha_id = factor_info.get("alpha_id", "")
            lifecycle = factor_info.get("lifecycle", "")
            report = factor_info.get("report")
            metrics = {}
            if report is not None and hasattr(report, "metrics"):
                metrics = report.metrics or {}
            factor_results.append({
                "alpha_id": alpha_id,
                "lifecycle": lifecycle,
                "metrics": metrics,
            })

        # Phase 3+: When no active factors exist, feed retired factors to
        # the feedback LLM so it can analyse failure patterns and produce
        # mining hints instead of skipping feedback entirely.
        if not factor_results and self._retired_factors:
            for entry in self._retired_factors[-20:]:
                factor_results.append({
                    "alpha_id": entry.get("alpha_id", "unknown"),
                    "lifecycle": "retired",
                    "metrics": {
                        "retired_reason": entry.get("reason", ""),
                        "sharpe": 0.0,
                    },
                })
            logger.info(
                "tick_feedback: no active factors; using %d retired factors "
                "for feedback analysis",
                len(factor_results),
            )

        if not factor_results:
            logger.debug("tick_feedback: no factor results to analyze")
            return

        # Attach the market-regime context (each entry carries the same
        # snapshot; the prompt reads it from the first entry).
        for result in factor_results:
            result["regime"] = self._regime_snapshot

        try:
            analysis = self._feedback.analyze(factor_results)
            if analysis:
                # Update mining hints for next cycle.
                hints = self._feedback.get_mining_hints()
                if hints:
                    self._mining_hints = hints
                    logger.info("tick_feedback: updated mining hints: %s", hints)
                else:
                    logger.debug("tick_feedback: no hints returned")
        except Exception as exc:  # noqa: BLE001
            logger.warning("tick_feedback: analysis error: %s", exc)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return pipeline state, active factors, and health status.

        Returns:
            Dict with pipeline_phase, tick_count, active_factors,
            pending_candidates, health_alive, health_stale, and
            memory usage info.
        """
        memory_info = self._memory_guard.check_memory()
        return {
            "pipeline_phase": self.pipeline_state.phase.value,
            "tick_count": self.pipeline_state.tick_count,
            "last_tick_at": (
                self.pipeline_state.last_tick_at.isoformat()
                if self.pipeline_state.last_tick_at else None
            ),
            "active_factors": len(self._active_factors),
            "pending_candidates": len(self._pending_candidates),
            "active_factor_ids": [
                f.get("alpha_id", "") for f in self._active_factors
            ],
            "health_alive": self.health.is_alive(),
            "health_stale": self.health.is_stale(),
            "memory": memory_info,
            "mining_hints": self._mining_hints,
            "risk_halted": self._risk_monitor.is_halted(),
            "config": {
                "pairs": self.config.pairs,
                "mine_interval_hours": self.config.mine_interval_hours,
                "evaluate_interval_hours": self.config.evaluate_interval_hours,
                "trade_interval_minutes": self.config.trade_interval_minutes,
                "feedback_interval_hours": self.config.feedback_interval_hours,
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _safe_tick(self, name: str, tick_fn: Any) -> None:
        """Run a tick function with error isolation.

        A single tick failure must never crash the loop. The error is
        logged and the pipeline state tick count is still incremented.

        Args:
            name: Tick name for logging.
            tick_fn: Async callable to invoke.
        """
        try:
            await tick_fn()
            self.pipeline_state = PipelineState(
                phase=self.pipeline_state.phase,
                active_factor_id=self.pipeline_state.active_factor_id,
                last_tick_at=_utc_now(),
                tick_count=self.pipeline_state.tick_count + 1,
                updated_at=_utc_now(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "tick %s failed: %s", name, exc, exc_info=True,
            )
            # Still increment tick count so memory guard fires periodically.
            self.pipeline_state = PipelineState(
                phase=self.pipeline_state.phase,
                active_factor_id=self.pipeline_state.active_factor_id,
                last_tick_at=_utc_now(),
                tick_count=self.pipeline_state.tick_count + 1,
                updated_at=_utc_now(),
            )

    def _set_phase(self, phase: PipelinePhase) -> None:
        """Update the pipeline phase in the state snapshot.

        Args:
            phase: The new pipeline phase.
        """
        self.pipeline_state = PipelineState(
            phase=phase,
            active_factor_id=self.pipeline_state.active_factor_id,
            last_tick_at=self.pipeline_state.last_tick_at,
            tick_count=self.pipeline_state.tick_count,
            updated_at=_utc_now(),
        )

    def _save_state(self) -> None:
        """Persist the current pipeline state via HealthMonitor."""
        try:
            # Phase 3: the latest market-regime classification rides along in
            # state.json so restart-time observers see it without re-classify.
            self.health.save_pipeline_state(
                self.pipeline_state,
                regime=self._regime_snapshot or None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to save pipeline state: %s", exc)
        # Factor snapshot for the observability API: active + pending + retired
        # factors with their screen IC direction and bench metrics.
        try:
            import json

            payload = {
                "active": [
                    {
                        "alpha_id": f.get("alpha_id", ""),
                        "lifecycle": f.get("lifecycle", ""),
                        "screen_ic_mean": _meta_value(
                            f.get("candidate"), "screen_ic_mean"
                        ),
                        **_bench_metrics(f.get("report")),
                    }
                    for f in self._active_factors
                ],
                "pending": [c.alpha_id for c in self._pending_candidates],
                "retired": list(self._retired_factors),
                "regime": self._regime_snapshot,
                "updated_at": _utc_now().isoformat(),
            }
            (self.health.runtime_root / "factors.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to save factor snapshot: %s", exc)

    def _execute_factor(
        self, candidate: FactorCandidate, panel: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a factor's compute function against a panel.

        Best-effort: returns ``None`` on any error.

        Args:
            candidate: The factor candidate with source code.
            panel: Panel to run against; defaults to the live panel.

        Returns:
            The factor DataFrame, or ``None`` on failure.
        """
        panel = self._panel if panel is None else panel
        try:
            # Build a temporary module from the full source.
            full_source = candidate.meta.get("full_module_source", "")
            if not full_source:
                return None

            import types as _types

            mod = _types.ModuleType(f"_tmp_{candidate.alpha_id}")
            mod.__dict__["__builtins__"] = __builtins__  # type: ignore[index]
            exec(compile(full_source, f"<{candidate.alpha_id}>", "exec"), mod.__dict__)

            compute_fn = getattr(mod, "compute", None)
            if compute_fn is None or not callable(compute_fn):
                return None

            result = compute_fn(panel)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "_execute_factor: %s failed: %s", candidate.alpha_id, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Phase 3: factor dedup / weighting / OOS recheck / regime
    # ------------------------------------------------------------------

    def _factor_dedup_check(
        self, candidate: FactorCandidate, panel: dict[str, Any],
    ) -> tuple[bool, str]:
        """Reject a candidate whose IC series duplicates an active factor.

        Both IC series are computed on the long evaluation panel so the
        redundancy test has statistical power. Any failure degrades to
        ``(False, "")`` — dedup never blocks promotion by accident.

        Args:
            candidate: The candidate that passed the overfit gate.
            panel: Long-window panel (history store) for IC computation.

        Returns:
            ``(rejected, reason)``; ``reason`` names the duplicate pair.
        """
        try:
            close = panel.get("close")
            if close is None or close.empty:
                return False, ""
            candidate_df = self._execute_factor(candidate, panel=panel)
            if candidate_df is None or candidate_df.empty:
                return False, ""
            cand_ic = compute_ic_series(close, candidate_df)
            entries: list[tuple[str, Any]] = []
            for info in self._active_factors:
                other = info.get("candidate")
                if other is None or other.alpha_id == candidate.alpha_id:
                    continue
                other_df = self._execute_factor(other, panel=panel)
                if other_df is None or other_df.empty:
                    continue
                entries.append(
                    (other.alpha_id, compute_ic_series(close, other_df))
                )
            return dedup_rejection_reason(
                candidate.alpha_id, cand_ic, entries,
                threshold=self.config.max_factor_correlation,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("factor dedup check failed: %s", exc)
            return False, ""

    def _factor_weight_map(self) -> dict[str, float]:
        """Per-factor order-notional weights for the trade tick.

        With ≤ 3 active factors, weights are proportional to |screen IC|
        (capped by ``max_single_factor_weight`` then renormalised); with
        more than 3 the plan falls back to equal weight to avoid any
        single factor dominating.

        Returns:
            ``{alpha_id: weight}`` summing to ~1.0.
        """
        factors = [
            f for f in self._active_factors
            if f.get("lifecycle") == FactorLifecycle.BACKTESTED.value
        ]
        n = len(factors)
        if n == 0:
            return {}
        if n == 1:
            # A single factor is the whole book — the cap only limits a
            # factor's *share of a multi-factor* notional.
            return {factors[0]["alpha_id"]: 1.0}
        if n > 3:
            w = 1.0 / n
            return {f["alpha_id"]: w for f in factors}
        ics: list[float] = []
        for f in factors:
            ic = _meta_value(f.get("candidate"), "screen_ic_mean")
            ics.append(abs(float(ic)) if ic is not None else 0.0)
        total = sum(ics)
        if total <= 0:
            w = 1.0 / n
            return {f["alpha_id"]: w for f in factors}
        cap = self.config.max_single_factor_weight
        raw = {
            f["alpha_id"]: ic / total for f, ic in zip(factors, ics)
        }
        if all(v <= cap + 1e-12 for v in raw.values()):
            return raw
        # Hard cap: clamp every factor that overshoots and redistribute
        # the surplus to the under-cap factors (proportional to their raw
        # share, themselves capped). Iterate so a redistribution that
        # pushes another factor over the cap gets clamped in turn.
        weights = dict(raw)
        for _ in range(n):
            over = [k for k, v in weights.items() if v > cap + 1e-12]
            if not over:
                break
            for k in over:
                weights[k] = cap
            surplus = sum(raw[k] for k in over) - cap * len(over)
            under = [k for k in weights if k not in over]
            under_raw = sum(raw[k] for k in under)
            if under_raw <= 0:
                break
            for k in under:
                weights[k] = min(
                    cap, raw[k] + surplus * raw[k] / under_raw,
                )
        return weights

    def _promotion_oos_recheck(self, factor_info: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Re-run the overfit gate on the long history window pre-promotion.

        The promotion gate only sees the paper period; this recheck reruns
        the full backtest on ``eval_bars`` of history and requires the
        walk-forward consistency gate to pass again. History being
        unavailable fails open (skip, log) so infrastructure issues never
        block promotion; a failed backtest fails closed (keep paper).

        Args:
            factor_info: An entry from ``self._active_factors``.

        Returns:
            ``(ok, details)`` — ``ok`` True promotes, False keeps paper.
        """
        candidate = factor_info.get("candidate")
        if candidate is None:
            return True, {"skipped": "no candidate"}
        try:
            panel = self._history.get_panel(
                self.config.pairs,
                period=self.config.bar_period,
                bars=self.config.eval_bars,
            )
            if not panel or "close" not in panel:
                logger.info(
                    "oos recheck: history unavailable for %s — skipping",
                    candidate.alpha_id,
                )
                return True, {"skipped": "history unavailable"}
            report = self._backtester.run_backtest_for_factor(candidate, panel)
            if report.status != "ok":
                return False, {
                    "reason": "OOS backtest failed: "
                    + str(report.metrics.get("error", "unknown")),
                }
            ok, details = self._overfit_gate.check(report)
            wf = details.get("gate3_walk_forward", {})
            rate = wf.get("consistency_rate")
            if not ok:
                return False, {
                    "ok": False,
                    "consistency_rate": rate,
                    "reason": f"OOS walk-forward consistency {rate} <= 0.6",
                    "bars": self.config.eval_bars,
                }
            return True, {
                "ok": True,
                "consistency_rate": rate,
                "bars": self.config.eval_bars,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("oos recheck failed for %s: %s", candidate.alpha_id, exc)
            return False, {"reason": f"OOS recheck error: {exc}"}

    def _current_regime(self) -> dict[str, Any]:
        """Classify the trailing market regime from the live panel.

        Best-effort: returns an ``unknown`` snapshot when no panel data is
        available so the pipeline state always carries a regime key.

        Returns:
            The regime dict from :func:`classify_regime`.
        """
        try:
            close = self._panel.get("close")
            if close is None:
                return {"regime": "unknown", "high_vol": False, "fused": None}
            return classify_regime(close)
        except Exception as exc:  # noqa: BLE001
            logger.debug("regime classification failed: %s", exc)
            return {"regime": "unknown", "high_vol": False, "fused": None}
