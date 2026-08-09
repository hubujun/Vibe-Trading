"""Real-time monitoring of paper-trading metrics.

Wraps :class:`PaperEngine` to expose threshold-based health checks
(Sharpe, drawdown, data gaps, kill-switch) that the autopilot pipeline
queries on every evaluation tick.  All checks are pure reads against the
engine's accumulated state — no orders are placed here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.crypto_autopilot.paper_engine import PaperEngine

logger = logging.getLogger(__name__)

__all__ = ["PaperMonitor"]


class PaperMonitor:
    """Monitor paper-trading health and detect promotion/retirement signals.

    Holds a reference to the :class:`PaperEngine` and evaluates
    threshold-based criteria on demand.  Intended to be polled by the
    autopilot pipeline every ``evaluate_interval_hours``.

    Attributes:
        engine: The :class:`PaperEngine` being monitored.
        config: Autopilot tuning knobs (kill-loss threshold, etc.).
    """

    def __init__(
        self,
        engine: PaperEngine,
        config: AutopilotConfig | None = None,
    ) -> None:
        """Initialize the monitor.

        Args:
            engine: Active paper-trading engine to observe.
            config: Autopilot config; loaded from env when ``None``.
        """
        self.engine: PaperEngine = engine
        self.config: AutopilotConfig = config or load_autopilot_config()

    # ------------------------------------------------------------------
    # Threshold checks
    # ------------------------------------------------------------------

    def check_sharpe(self, threshold: float = 1.0) -> tuple[bool, float]:
        """Evaluate the rolling 30-day Sharpe ratio.

        Args:
            threshold: Minimum acceptable annualised Sharpe.

        Returns:
            ``(passes, sharpe_value)`` — ``passes`` is ``True`` when
            *sharpe_value* >= *threshold*.
        """
        sharpe = self.engine.compute_rolling_sharpe(window_days=30)
        return sharpe >= threshold, sharpe

    def check_max_drawdown(self, threshold: float = 0.15) -> tuple[bool, float]:
        """Evaluate the maximum drawdown from the cumulative P&L curve.

        Args:
            threshold: Maximum acceptable drawdown as a fraction
                (0.15 = 15%).

        Returns:
            ``(within_threshold, drawdown_value)`` — ``within_threshold``
            is ``True`` when *drawdown_value* <= *threshold*.
        """
        dd = self.engine.compute_max_drawdown()
        return dd <= threshold, dd

    def check_data_gaps(self, bars: pd.DataFrame) -> list[str]:
        """Detect missing dates in a K-line DataFrame.

        Expects a DataFrame with a ``DatetimeIndex`` (as returned by
        :meth:`MarketFeed.fetch_bars`).  Gaps are reported when the
        difference between consecutive index values exceeds the
        expected daily cadence (> 1 day + 2h tolerance).

        Args:
            bars: OHLCV DataFrame with a ``DatetimeIndex``.

        Returns:
            List of human-readable gap descriptions (empty when the
            series is contiguous).
        """
        if bars is None or bars.empty or len(bars) < 2:
            return []

        idx = pd.DatetimeIndex(bars.index)
        diffs = idx.to_series().diff().dt.total_seconds()
        # Expected daily cadence: 86400 s. Allow 2h tolerance.
        tolerance_s = 86400 + 7200
        gaps: list[str] = []
        for i in range(1, len(diffs)):
            gap_s = diffs.iloc[i]
            if gap_s > tolerance_s:
                prev_date = idx[i - 1].strftime("%Y-%m-%d")
                curr_date = idx[i].strftime("%Y-%m-%d")
                missing_days = int(gap_s / 86400) - 1
                gaps.append(
                    f"gap between {prev_date} and {curr_date} "
                    f"(~{missing_days} missing day(s))"
                )
        return gaps

    def check_kill_switch(self) -> bool:
        """Determine whether the kill-loss threshold has been breached.

        Compares today's P&L against the configured
        :attr:`config.kill_loss_pct` (default 5%).  A breach means the
        autopilot loop should halt new order placement until manually
        reset.

        Returns:
            ``True`` when the kill switch should be triggered (daily
            loss exceeds the threshold).
        """
        try:
            daily_pnl = self.engine.compute_daily_pnl()
        except Exception as exc:  # noqa: BLE001 — monitor must not crash
            logger.warning("check_kill_switch: compute_daily_pnl failed: %s", exc)
            return False

        # Use total exposure as the denominator; fall back to 1 USD to
        # avoid division by zero when no positions are open.
        positions = self.engine.get_positions()
        total_notional = sum(
            p.entry_price * p.quantity for p in positions
        )
        if total_notional <= 0:
            total_notional = 1.0

        loss_pct = abs(min(daily_pnl, 0.0)) / total_notional * 100.0
        triggered = loss_pct >= self.config.kill_loss_pct
        if triggered:
            logger.warning(
                "kill switch triggered: daily loss %.2f%% >= %.2f%%",
                loss_pct,
                self.config.kill_loss_pct,
            )
        return triggered

    # ------------------------------------------------------------------
    # Aggregate status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return a snapshot of all monitored metrics.

        Returns:
            Dict with keys: ``rolling_sharpe``, ``max_drawdown``,
            ``days_active``, ``daily_pnl_last``, ``positions_count``,
            ``kill_switch_triggered``.
        """
        history = self.engine.get_daily_pnl_history()
        days_active = len(history)
        last_pnl = history[-1][1] if history else 0.0
        positions = self.engine.get_positions()
        sharpe = self.engine.compute_rolling_sharpe(window_days=30)
        dd = self.engine.compute_max_drawdown()

        return {
            "rolling_sharpe": round(sharpe, 4),
            "max_drawdown": round(dd, 6),
            "days_active": days_active,
            "daily_pnl_last": round(last_pnl, 4),
            "positions_count": len(positions),
            "kill_switch_triggered": self.check_kill_switch(),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
