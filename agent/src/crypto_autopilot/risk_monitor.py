"""Autopilot-specific risk monitoring with auto-halt.

Wraps the project's filesystem kill switch (:mod:`src.live.halt`) with
autopilot-tuned loss checks. The monitor is called every trade tick to
evaluate two conditions that trip the halt:

1. **Daily loss** — when today's equity drawdown exceeds
   :attr:`AutopilotConfig.kill_loss_pct` (default 5%). This is the hard
   stop-loss that unwinds the autopilot before a bad day compounds.

2. **Consecutive losses** — when the last *N* daily P&L values are all
   negative (default 3). A losing streak signals a regime the current
   factors cannot handle, so the autopilot halts until the operator
   re-authorizes.

Both checks trip the same filesystem sentinel (:func:`src.live.halt.trip_halt`)
so the enforcement gate (:mod:`src.live.enforcement`) and the live runner
(:class:`src.live.runtime.runner.LiveRunner`) observe it through their
existing halt-check path — no new coordination surface.

The monitor is deliberately **fail-safe**: a risk-check *error* (e.g. a
NaN equity value) trips the halt rather than silently allowing trading to
continue. A monitoring failure must never become a trading free-pass.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.live.halt import clear_halt as _clear_halt
from src.live.halt import halt_flag_set
from src.live.halt import trip_halt

logger = logging.getLogger(__name__)

__all__ = ["RiskMonitor"]

#: Broker key for the autopilot live channel — must match the mandate template
#: and the OKX connector so the per-broker HALT sentinel aligns.
_BROKER_KEY = "okx"

#: Number of consecutive negative daily P&L entries that trip the halt.
_CONSECUTIVE_LOSS_DAYS = 3

#: Trip source label recorded in the HALT sentinel's ``by`` field.
_HALT_TRIP_SOURCE = "cli"


class RiskMonitor:
    """Autopilot risk monitor backed by the filesystem kill switch.

    The monitor owns no trading state — it is a pure evaluator that takes
    equity snapshots and P&L history as inputs and trips/clears the HALT
    sentinel via :mod:`src.live.halt`. This keeps the kill switch
    LLM-independent (a pure file check) while letting the autopilot loop
    drive risk decisions from its own metrics.

    Attributes:
        config: Autopilot config (kill-loss threshold, pairs, etc.).
        broker: Broker key whose sentinel is tripped/cleared.
    """

    def __init__(
        self,
        config: AutopilotConfig | None = None,
        halt_dir: Path | None = None,
    ) -> None:
        """Initialize the risk monitor.

        Args:
            config: Autopilot config; loaded from env when ``None``. The
                ``kill_loss_pct`` field controls the daily-drawdown trip
                threshold.
            halt_dir: **Deprecated** — kept for API symmetry with older
                configs. The HALT sentinel path is resolved by
                :mod:`src.live.halt` from the runtime root, so this argument
                is informational only and does not override the sentinel
                location. It may be used by tests to assert the directory
                exists.
        """
        self.config: AutopilotConfig = config or load_autopilot_config()
        self.broker: str = _BROKER_KEY

        # The halt_dir is recorded for observability but the actual sentinel
        # path is owned by src.live.halt (resolved from the runtime root).
        # This avoids a second source of truth for the kill-switch location.
        self._halt_dir: Path | None = halt_dir

    # ------------------------------------------------------------------
    # Daily loss check
    # ------------------------------------------------------------------

    def check_daily_loss(
        self,
        current_equity: float,
        start_of_day_equity: float,
    ) -> tuple[bool, float]:
        """Check whether today's drawdown exceeds the kill-loss threshold.

        The daily loss percentage is::

            (start_of_day - current) / start_of_day * 100

        When the loss exceeds :attr:`config.kill_loss_pct`, the HALT sentinel
        is tripped and the monitor returns ``(True, loss_pct)``.

        **Fail-safe**: when ``start_of_day_equity`` is non-positive or either
        value is NaN, the monitor trips the halt (ambiguous state → stop
        trading) and returns ``loss_pct = 0.0`` to avoid masking the real
        issue behind a misleading number.

        Args:
            current_equity: Current account equity in USD.
            start_of_day_equity: Equity at UTC midnight (the day's baseline).

        Returns:
            A ``(halt_triggered, loss_pct)`` tuple.
        """
        try:
            baseline = float(start_of_day_equity)
            current = float(current_equity)
        except (TypeError, ValueError):
            logger.error(
                "check_daily_loss: unparseable equity values "
                "(start=%r, current=%r) — tripping halt (fail-safe)",
                start_of_day_equity,
                current_equity,
            )
            self.trigger_halt("unparseable equity value in daily-loss check")
            return True, 0.0

        # NaN or non-positive baseline → ambiguous, trip the halt.
        if baseline != baseline or baseline <= 0:
            logger.error(
                "check_daily_loss: invalid start_of_day_equity %.2f — "
                "tripping halt (fail-safe)",
                baseline,
            )
            self.trigger_halt("invalid start-of-day equity in daily-loss check")
            return True, 0.0

        if current != current:
            logger.error(
                "check_daily_loss: NaN current_equity — tripping halt "
                "(fail-safe)"
            )
            self.trigger_halt("NaN current equity in daily-loss check")
            return True, 0.0

        loss_pct = (baseline - current) / baseline * 100.0

        if loss_pct > self.config.kill_loss_pct:
            reason = (
                f"daily loss {loss_pct:.2f}% exceeds kill threshold "
                f"{self.config.kill_loss_pct:.2f}%"
            )
            logger.warning("risk monitor: %s — tripping halt", reason)
            self.trigger_halt(reason)
            return True, loss_pct

        return False, loss_pct

    # ------------------------------------------------------------------
    # Consecutive loss check
    # ------------------------------------------------------------------

    def check_consecutive_losses(
        self,
        daily_pnls: list[float],
    ) -> tuple[bool, int]:
        """Check whether the last *N* daily P&L values are all negative.

        A sustained losing streak signals a market regime the current
        factors cannot handle. When the last
        :data:`_CONSECUTIVE_LOSS_DAYS` entries are all negative, the HALT is
        tripped so the operator can intervene before the drawdown deepens.

        Args:
            daily_pnls: List of daily P&L values (USD), most-recent last.
                Values of exactly ``0.0`` are treated as non-negative (a
                flat day does not extend a losing streak).

        Returns:
            A ``(halt_triggered, consecutive_loss_days)`` tuple. When the
            list has fewer entries than the streak threshold, returns
            ``(False, 0)``.
        """
        if not daily_pnls or len(daily_pnls) < _CONSECUTIVE_LOSS_DAYS:
            return False, 0

        # Count the trailing consecutive negative days.
        streak = 0
        for pnl in reversed(daily_pnls):
            try:
                value = float(pnl)
            except (TypeError, ValueError):
                logger.error(
                    "check_consecutive_losses: unparseable P&L %r — "
                    "tripping halt (fail-safe)",
                    pnl,
                )
                self.trigger_halt("unparseable P&L value in consecutive-loss check")
                return True, 0

            if value < 0:
                streak += 1
            else:
                break

        if streak >= _CONSECUTIVE_LOSS_DAYS:
            reason = (
                f"consecutive negative days ({streak}) >= "
                f"threshold ({_CONSECUTIVE_LOSS_DAYS})"
            )
            logger.warning("risk monitor: %s — tripping halt", reason)
            self.trigger_halt(reason)
            return True, streak

        return False, streak

    # ------------------------------------------------------------------
    # Kill-switch control
    # ------------------------------------------------------------------

    def trigger_halt(self, reason: str) -> None:
        """Trip the filesystem kill switch for the autopilot broker.

        Delegates to :func:`src.live.halt.trip_halt`, which writes a JSON
        sentinel at ``<runtime_root>/live/<broker>/HALT``. The sentinel's
        existence (not its contents) is what enforces the halt, so even a
        partial write is fail-closed.

        Tripping is idempotent: a fresh sentinel overwrites any prior one,
        recording the latest reason. The trip is logged at WARNING level.

        Args:
            reason: Human-readable reason recorded in the sentinel for the
                audit trail.
        """
        try:
            trip_halt(
                _HALT_TRIP_SOURCE,
                reason,
                broker=self.broker,
            )
        except Exception:
            # A trip failure must not be swallowed silently — but the caller
            # (the autopilot tick loop) should still see a halted state on
            # the next is_halted() check. Log loudly and re-raise so the tick
            # loop can catch and abort.
            logger.exception(
                "risk monitor: FAILED to trip halt for %s — "
                "operator must manually create the HALT file",
                self.broker,
            )
            raise

    def is_halted(self) -> bool:
        """Return whether the kill switch is currently tripped.

        Delegates to :func:`src.live.halt.halt_flag_set` — a pure filesystem
        check with no LLM or in-process dependency. The global sentinel
        (``<runtime_root>/live/HALT``) always wins; the per-broker sentinel
        (``<runtime_root>/live/<broker>/HALT``) is also consulted.

        Returns:
            ``True`` if trading is halted (globally or for this broker).
        """
        return halt_flag_set(self.broker)

    def clear_halt(self) -> None:
        """Clear the per-broker kill switch sentinel (operator action).

        Delegates to :func:`src.live.halt.clear_halt`. Clearing is a
        privileged surface action — the autopilot loop never calls this; it
        is provided for the operator / CLI to resume trading after
        investigating the halt reason.

        Only the per-broker sentinel is cleared; the global sentinel (if
        any) must be cleared separately.

        Raises:
            FileNotFoundError: Silently caught — clearing when no sentinel
                exists is a no-op (returns without error).
        """
        try:
            removed = _clear_halt(broker=self.broker)
            if removed:
                logger.info(
                    "risk monitor: halt cleared for %s by operator",
                    self.broker,
                )
            else:
                logger.info(
                    "risk monitor: clear_halt called but no sentinel "
                    "existed for %s",
                    self.broker,
                )
        except Exception:
            logger.exception(
                "risk monitor: failed to clear halt for %s", self.broker
            )
            raise

    # ------------------------------------------------------------------
    # Combined evaluation (called every tick)
    # ------------------------------------------------------------------

    def evaluate(
        self,
        current_equity: float,
        start_of_day_equity: float,
        daily_pnls: list[float],
    ) -> dict[str, Any]:
        """Run all risk checks and return a status snapshot.

        This is the main entry point called every trade tick. It runs both
        the daily-loss and consecutive-loss checks, then returns a dict the
        autopilot loop uses to decide whether to proceed or abort.

        If the HALT is already tripped (from a prior tick or an external
        source), the checks are skipped and the status reflects the existing
        halted state — the monitor does not re-trip an already-tripped
        switch.

        Args:
            current_equity: Current account equity in USD.
            start_of_day_equity: Equity at UTC midnight.
            daily_pnls: List of daily P&L values (USD), most-recent last.

        Returns:
            A status dict with keys:

            - ``daily_loss_pct``: today's drawdown percentage (``float``).
            - ``halt_triggered``: whether the halt is active (``bool``).
            - ``consecutive_losses``: trailing negative-day streak (``int``).
            - ``reason``: human-readable halt reason (empty when not halted).
            - ``ts``: ISO-8601 UTC evaluation timestamp.
        """
        now = datetime.now(timezone.utc)

        # If already halted, short-circuit — no need to re-evaluate or
        # re-trip. The sentinel's existence is authoritative.
        if self.is_halted():
            return {
                "daily_loss_pct": 0.0,
                "halt_triggered": True,
                "consecutive_losses": 0,
                "reason": "halt already active (pre-existing sentinel)",
                "ts": now.isoformat(timespec="seconds"),
            }

        loss_halt, loss_pct = self.check_daily_loss(
            current_equity, start_of_day_equity
        )

        streak_halt, streak = self.check_consecutive_losses(daily_pnls)

        halt_triggered = loss_halt or streak_halt
        reasons: list[str] = []
        if loss_halt:
            reasons.append(
                f"daily loss {loss_pct:.2f}% > "
                f"{self.config.kill_loss_pct:.2f}%"
            )
        if streak_halt:
            reasons.append(
                f"consecutive losses ({streak} days >= "
                f"{_CONSECUTIVE_LOSS_DAYS})"
            )

        return {
            "daily_loss_pct": loss_pct,
            "halt_triggered": halt_triggered,
            "consecutive_losses": streak,
            "reason": "; ".join(reasons) if reasons else "",
            "ts": now.isoformat(timespec="seconds"),
        }
