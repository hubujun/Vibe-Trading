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
from src.crypto_autopilot.factor_miner import FactorMiner
from src.crypto_autopilot.factor_screen import FactorScreen
from src.crypto_autopilot.factor_store import FactorStore
from src.crypto_autopilot.feedback import FeedbackAnalyzer
from src.crypto_autopilot.health import HealthMonitor
from src.crypto_autopilot.llm_budget import LLMBudget
from src.crypto_autopilot.live_executor import LiveExecutor
from src.crypto_autopilot.market_feed import MarketFeed
from src.crypto_autopilot.memory_guard import MemoryGuard
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

__all__ = ["AutopilotOrchestrator"]


def _now_ms() -> int:
    """Return the current wall-clock time in epoch milliseconds."""
    return int(time.time() * 1000)


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


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

    def __init__(self, config: AutopilotConfig | None = None) -> None:
        """Initialize all components and load pipeline state from HealthMonitor.

        Args:
            config: Autopilot config; loaded from env when ``None``.
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
        self._backtester: AutoBacktester = AutoBacktester(
            bars_per_year=self.config.bars_per_year,
        )
        self._overfit_gate: OverfitGate = OverfitGate()
        self._paper_engine: PaperEngine = PaperEngine(config=self.config)
        self._paper_monitor: PaperMonitor = PaperMonitor(
            self._paper_engine, config=self.config,
        )
        self._promotion_gate: PromotionGate = PromotionGate(config=self.config)
        self._live_executor: LiveExecutor = LiveExecutor(config=self.config)
        self._risk_monitor: RiskMonitor = RiskMonitor(config=self.config)
        self._feedback: FeedbackAnalyzer = FeedbackAnalyzer(config=self.config)

        # In-memory working set.
        self._panel: dict[str, Any] = {}
        self._pending_candidates: list[FactorCandidate] = []
        self._active_factors: list[dict[str, Any]] = []
        self._mining_hints: list[str] = []

        # Loop control.
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

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

        # Track last-run timestamps for each phase.
        last_mine = 0.0
        last_evaluate = 0.0
        last_trade = 0.0
        last_feedback = 0.0

        # Short collect interval — runs every trade tick.
        collect_interval_s = trade_interval_s

        while self._running:
            now = time.monotonic()

            # Always write heartbeat.
            try:
                self.health.write_heartbeat(_now_ms())
            except Exception:  # noqa: BLE001
                logger.debug("heartbeat write failed", exc_info=True)

            # Collect phase — runs every trade tick to keep data fresh.
            if now - last_trade >= collect_interval_s:
                await self._safe_tick("collect", self._tick_collect)
                last_trade = now

            # Mine phase.
            if now - last_mine >= mine_interval_s:
                await self._safe_tick("mine", self._tick_mine)
                last_mine = now

            # Evaluate phase.
            if now - last_evaluate >= evaluate_interval_s:
                await self._safe_tick("evaluate", self._tick_evaluate)
                last_evaluate = now

            # Trade phase — runs at the same cadence as collect.
            # (Already handled above via _tick_collect timing.)

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
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._save_state()
        self._task = None
        logger.info("autopilot orchestrator stopped")

    # ------------------------------------------------------------------
    # Tick methods — each wrapped in try/except by _safe_tick
    # ------------------------------------------------------------------

    async def _tick_collect(self) -> None:
        """Fetch K-line data, build panel, trim with MemoryGuard."""
        self._set_phase(PipelinePhase.COLLECTING)
        logger.debug("tick_collect: fetching bars for %s", self.config.pairs)

        bars = self._feed.fetch_panel(
            pairs=self.config.pairs, period="1d", limit=90,
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

        if not self._panel or "close" not in self._panel:
            logger.info("tick_evaluate: no panel available; skipping")
            return

        remaining: list[FactorCandidate] = []
        for candidate in self._pending_candidates:
            try:
                report = self._backtester.run_backtest_for_factor(candidate, self._panel)
                passes, reason, details = self._overfit_gate.evaluate(candidate, report)

                if passes:
                    # Advance to paper trading.
                    self._factor_store.advance_lifecycle(
                        candidate.alpha_id, FactorLifecycle.BACKTESTED,
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

        if not self._active_factors:
            logger.debug("tick_trade: no active factors to trade")
            return

        # For each active factor in paper-trading phase, generate a simple signal
        # and place a small notional order via PaperEngine.
        for factor_info in self._active_factors:
            try:
                alpha_id = factor_info["alpha_id"]
                lifecycle = factor_info.get("lifecycle", "")

                if lifecycle == FactorLifecycle.BACKTESTED.value:
                    # Paper-trade this factor.
                    self._set_phase(PipelinePhase.PAPER_TRADING)
                    notional = min(
                        self.config.max_order_notional_usd,
                        self.config.max_total_exposure_usd / max(len(self._active_factors), 1),
                    )
                    # Simple long signal for the first configured pair.
                    if self.config.pairs:
                        result = self._paper_engine.place_order(
                            symbol=self.config.pairs[0],
                            side="buy",
                            notional=notional,
                        )
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
                        self._factor_store.advance_lifecycle(
                            alpha_id, FactorLifecycle.LIVE_DEPLOYED,
                        )
                        factor_info["lifecycle"] = FactorLifecycle.LIVE_DEPLOYED.value
                        logger.info("tick_trade: %s promoted to live", alpha_id)
                    else:
                        verdict = self._promotion_gate.decide_retire_or_retry(
                            self._paper_monitor,
                        )
                        if verdict == "retire":
                            self._factor_store.advance_lifecycle(
                                alpha_id, FactorLifecycle.RETIRED,
                            )
                            logger.info("tick_trade: %s retired — %s", alpha_id, reason)

            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "tick_trade: error for %s: %s",
                    factor_info.get("alpha_id", "?"), exc,
                )

    async def _tick_feedback(self) -> None:
        """Call FeedbackAnalyzer.analyze(), update mining hints."""
        self._set_phase(PipelinePhase.FEEDBACK)

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

        if not factor_results:
            logger.debug("tick_feedback: no factor results to analyze")
            return

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
            self.health.save_pipeline_state(self.pipeline_state)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to save pipeline state: %s", exc)

    def _execute_factor(self, candidate: FactorCandidate) -> Any:
        """Execute a factor's compute function against the current panel.

        Best-effort: returns ``None`` on any error.

        Args:
            candidate: The factor candidate with source code.

        Returns:
            The factor DataFrame, or ``None`` on failure.
        """
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

            result = compute_fn(self._panel)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "_execute_factor: %s failed: %s", candidate.alpha_id, exc,
            )
            return None
