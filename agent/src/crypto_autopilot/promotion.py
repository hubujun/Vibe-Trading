"""Promotion gate: paper → live factor graduation and retirement rules.

A factor candidate advances from :attr:`FactorLifecycle.PAPER_VALIDATED`
to :attr:`FactorLifecycle.LIVE_DEPLOYED` only when *all* of the
following hold for the configured observation window:

* **Days active** ≥ ``config.paper_min_days`` (default 14)
* **Rolling Sharpe** > 1.0 (annualised, 365-day basis)
* **Maximum drawdown** < 15%

When a factor clearly underperforms (negative Sharpe or drawdown > 25%)
the gate returns a ``"retire"`` verdict so the pipeline can archive it.
Borderline factors receive a ``"retry"`` verdict — the paper run
continues without promotion.
"""

from __future__ import annotations

import logging

from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.crypto_autopilot.paper_monitor import PaperMonitor

logger = logging.getLogger(__name__)

__all__ = ["PromotionGate"]

#: Sharpe threshold for promotion to live deployment.
_PROMOTION_SHARPE: float = 1.0

#: Maximum drawdown threshold for promotion (fraction, 0.15 = 15%).
_PROMOTION_MAX_DD: float = 0.15

#: Drawdown above which a factor is retired outright.
_RETIRE_MAX_DD: float = 0.25

#: Number of days after which a clearly-stagnant factor is retired.
_STAGNANT_DAYS: int = 14

#: Sharpe below which a factor is considered stagnant after _STAGNANT_DAYS.
_STAGNANT_SHARPE: float = 0.1


class PromotionGate:
    """Decide whether a paper-traded factor should be promoted, retired, or retried.

    All decisions are pure functions of the metrics exposed by
    :class:`PaperMonitor` — no side effects, no broker calls.

    Attributes:
        config: Autopilot tuning knobs (``paper_min_days``, etc.).
    """

    def __init__(self, config: AutopilotConfig | None = None) -> None:
        """Initialize the promotion gate.

        Args:
            config: Autopilot config; loaded from env when ``None``.
        """
        self.config: AutopilotConfig = config or load_autopilot_config()

    # ------------------------------------------------------------------
    # Promotion evaluation
    # ------------------------------------------------------------------

    def evaluate(self, monitor: PaperMonitor) -> tuple[bool, str, dict]:
        """Check all promotion criteria and return a structured verdict.

        Criteria evaluated:

        1. ``days_active >= config.paper_min_days``
        2. ``rolling_sharpe > 1.0``
        3. ``max_drawdown < 0.15``

        Args:
            monitor: The :class:`PaperMonitor` observing the paper run.

        Returns:
            ``(promoted, reason, details)`` where:

            * *promoted* is ``True`` only when every criterion passes.
            * *reason* is ``"All criteria met"`` on success, or the
              first failing criterion's description.
            * *details* is a dict mapping each criterion name to its
              ``{value, threshold, passed}`` triple.
        """
        status = monitor.get_status()
        days_active: int = status.get("days_active", 0)
        sharpe: float = status.get("rolling_sharpe", 0.0)
        max_dd: float = status.get("max_drawdown", 0.0)

        criteria: dict[str, dict] = {
            "days_active": {
                "value": days_active,
                "threshold": self.config.paper_min_days,
                "passed": days_active >= self.config.paper_min_days,
            },
            "rolling_sharpe": {
                "value": round(sharpe, 4),
                "threshold": _PROMOTION_SHARPE,
                "passed": sharpe > _PROMOTION_SHARPE,
            },
            "max_drawdown": {
                "value": round(max_dd, 6),
                "threshold": _PROMOTION_MAX_DD,
                "passed": max_dd < _PROMOTION_MAX_DD,
            },
        }

        # First failing criterion determines the reason string.
        failing = [
            name for name, c in criteria.items() if not c["passed"]
        ]
        promoted = len(failing) == 0
        if promoted:
            reason = "All criteria met"
        else:
            first = failing[0]
            c = criteria[first]
            reason = (
                f"{first} not met: value={c['value']}, "
                f"threshold={c['threshold']}"
            )

        details: dict = {
            "promoted": promoted,
            "criteria": criteria,
            "kill_switch_triggered": status.get("kill_switch_triggered", False),
            "positions_count": status.get("positions_count", 0),
            "daily_pnl_last": status.get("daily_pnl_last", 0.0),
        }

        logger.info(
            "PromotionGate.evaluate: promoted=%s reason=%r", promoted, reason
        )
        return promoted, reason, details

    # ------------------------------------------------------------------
    # Retire / retry decision
    # ------------------------------------------------------------------

    def decide_retire_or_retry(self, monitor: PaperMonitor) -> str:
        """Decide whether a paper-traded factor should retire, retry, or continue.

        Decision logic:

        * **retire** — Sharpe < 0 (negative returns) OR max drawdown
          > 25%.  The factor is clearly harmful.
        * **retire** — Sharpe near zero (< 0.1) for ≥ 14 days.  The
          factor is stagnant and consuming observation slots.
        * **retry** — Criteria are close but not yet met (e.g. Sharpe
          between 0.5 and 1.0, or days_active just below the minimum).
          Keep running.
        * **continue** — Not enough data yet to form a judgment.

        Args:
            monitor: The :class:`PaperMonitor` observing the paper run.

        Returns:
            One of ``"retire"``, ``"retry"``, or ``"continue"``.
        """
        status = monitor.get_status()
        days_active: int = status.get("days_active", 0)
        sharpe: float = status.get("rolling_sharpe", 0.0)
        max_dd: float = status.get("max_drawdown", 0.0)

        # Hard retirement: clearly harmful.
        if sharpe < 0.0:
            logger.info(
                "decide_retire_or_retry: retire (negative sharpe=%.4f)", sharpe
            )
            return "retire"

        if max_dd > _RETIRE_MAX_DD:
            logger.info(
                "decide_retire_or_retry: retire (max_dd=%.4f > %.2f)",
                max_dd,
                _RETIRE_MAX_DD,
            )
            return "retire"

        # Stagnant retirement: no alpha after sufficient observation.
        if days_active >= _STAGNANT_DAYS and sharpe < _STAGNANT_SHARPE:
            logger.info(
                "decide_retire_or_retry: retire (stagnant: days=%d sharpe=%.4f)",
                days_active,
                sharpe,
            )
            return "retire"

        # Retry: criteria close but not met.
        promoted, _, _ = self.evaluate(monitor)
        if not promoted and days_active >= self.config.paper_min_days:
            # Has enough data but didn't pass — check if it's borderline.
            sharpe_close = sharpe > 0.5
            dd_close = max_dd < 0.20
            if sharpe_close or dd_close:
                logger.info(
                    "decide_retire_or_retry: retry (sharpe=%.4f dd=%.4f)",
                    sharpe,
                    max_dd,
                )
                return "retry"

        # Not enough data yet — keep running.
        logger.info(
            "decide_retire_or_retry: continue (days=%d sharpe=%.4f dd=%.4f)",
            days_active,
            sharpe,
            max_dd,
        )
        return "continue"
