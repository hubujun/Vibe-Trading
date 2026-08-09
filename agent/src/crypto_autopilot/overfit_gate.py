"""Three-gate overfitting control + Harvey-Liu-Zhu multi-testing correction.

:class:`OverfitGate` is the admission gate between a mined crypto factor's
backtest and its promotion to paper trading.  It combines two independent
layers of defence against overfitting:

**Layer 1 — Three statistical validation gates** (all must pass):
    * Gate 1 (Monte Carlo permutation test): ``p_value_sharpe < 0.05`` — the
      strategy's Sharpe ratio is significantly better than a random
      reordering of the same trades.
    * Gate 2 (Bootstrap Sharpe CI): ``ci_lower > 0`` — the lower bound of
      the 95% bootstrap confidence interval for the Sharpe ratio is
      strictly positive.
    * Gate 3 (Walk-forward consistency): ``consistency_rate > 0.6`` — more
      than 60% of non-overlapping time windows are profitable.

**Layer 2 — Harvey-Liu-Zhu (2016) multiple-testing correction**:
    Uses :func:`run_bench_strict` with ``alpha_t_threshold=3.5`` (the
    median |t| threshold recommended when correcting for the full factor
    zoo).  Only factors categorised as ``confirmed_alive`` by
    :func:`categorise_strict` survive this gate — meaning the factor's
    information coefficient (IC) must beat a same-universe random control
    both in-sample and (optionally) out-of-sample.

References:
    Harvey, C. R., Liu, Y., & Zhu, H. (2016). “…and the Cross-Section of
    Expected Returns”. *Review of Financial Studies*, 29(1), 43–86.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.crypto_autopilot.types import BacktestReport, FactorCandidate

logger = logging.getLogger(__name__)

__all__ = ["OverfitGate"]

#: Gate 1 threshold: Monte Carlo p-value for Sharpe must be below this.
_MC_P_VALUE_THRESHOLD: float = 0.05

#: Gate 2 threshold: Bootstrap CI lower bound must be strictly positive.
_BOOTSTRAP_CI_LOWER_THRESHOLD: float = 0.0

#: Gate 3 threshold: Walk-forward consistency rate must exceed this.
_WF_CONSISTENCY_THRESHOLD: float = 0.6

#: Harvey-Liu-Zhu (2016) recommended |t| threshold for a large factor zoo.
_HLZ_ALPHA_T_THRESHOLD: float = 3.5

#: The strict category that passes the multi-testing correction.
_PASSING_CATEGORY: str = "confirmed_alive"


class OverfitGate:
    """Admission gate combining statistical validation and multiple-testing correction.

    The gate is stateless — each call is independent.  Use
    :meth:`evaluate` for the combined verdict, or call :meth:`check` and
    :meth:`check_bench_strict` separately for granular control.
    """

    # ------------------------------------------------------------------
    # Layer 1: Three statistical validation gates
    # ------------------------------------------------------------------

    def check(self, report: BacktestReport) -> tuple[bool, dict[str, Any]]:
        """Evaluate the three statistical validation gates.

        Gate 1: ``report.validation["monte_carlo"]["p_value_sharpe"] < 0.05``
        Gate 2: ``report.validation["bootstrap"]["ci_lower"] > 0``
        Gate 3: ``report.validation["walk_forward"]["consistency_rate"] > 0.6``

        Args:
            report: The backtest report containing validation results.

        Returns:
            Tuple of ``(all_pass, details)`` where ``details`` has per-gate
            pass/fail booleans and the raw threshold values.
        """
        validation = report.validation

        # Handle missing or error validation results.
        mc = validation.get("monte_carlo", {})
        bs = validation.get("bootstrap", {})
        wf = validation.get("walk_forward", {})

        # Gate 1: Monte Carlo p-value.
        mc_p_value = mc.get("p_value_sharpe")
        gate1_pass = (
            isinstance(mc_p_value, (int, float))
            and mc_p_value < _MC_P_VALUE_THRESHOLD
        )

        # Gate 2: Bootstrap CI lower bound.
        bs_ci_lower = bs.get("ci_lower")
        gate2_pass = (
            isinstance(bs_ci_lower, (int, float))
            and bs_ci_lower > _BOOTSTRAP_CI_LOWER_THRESHOLD
        )

        # Gate 3: Walk-forward consistency.
        wf_rate = wf.get("consistency_rate")
        gate3_pass = (
            isinstance(wf_rate, (int, float))
            and wf_rate > _WF_CONSISTENCY_THRESHOLD
        )

        all_pass = gate1_pass and gate2_pass and gate3_pass

        details: dict[str, Any] = {
            "gate1_monte_carlo": {
                "pass": gate1_pass,
                "p_value_sharpe": mc_p_value,
                "threshold": _MC_P_VALUE_THRESHOLD,
            },
            "gate2_bootstrap": {
                "pass": gate2_pass,
                "ci_lower": bs_ci_lower,
                "threshold": _BOOTSTRAP_CI_LOWER_THRESHOLD,
            },
            "gate3_walk_forward": {
                "pass": gate3_pass,
                "consistency_rate": wf_rate,
                "threshold": _WF_CONSISTENCY_THRESHOLD,
            },
            "all_pass": all_pass,
        }

        if not all_pass:
            failed = [k for k, v in details.items() if isinstance(v, dict) and not v.get("pass", True)]
            logger.info(
                "OverfitGate: validation gates failed for %s — %s",
                report.alpha_id,
                failed,
            )

        return all_pass, details

    # ------------------------------------------------------------------
    # Layer 2: Harvey-Liu-Zhu multi-testing correction
    # ------------------------------------------------------------------

    def check_bench_strict(
        self,
        alpha_id: str,
        period: str,
        oos_split: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Run the strict bench with Harvey-Liu-Zhu multi-testing correction.

        Calls :func:`run_bench_strict` with ``alpha_t_threshold=3.5`` (the
        Harvey-Liu-Zhu 2016 recommended threshold for a large factor zoo)
        and checks whether the factor is categorised as
        ``confirmed_alive`` by :func:`categorise_strict`.

        Args:
            alpha_id: The factor identifier to check.
            period: Data period string (``YYYY-YYYY`` or
                ``YYYY-MM-DD/YYYY-MM-DD``).
            oos_split: Optional out-of-sample split date
                (``YYYY-MM-DD``).  When provided, the factor must survive
                in both train and test periods.

        Returns:
            Tuple of ``(is_confirmed, details)`` where ``details``
            includes the bench result summary and the factor's category.
        """
        from src.factors.bench_runner_strict import (
            StrictThresholds,
            run_bench_strict,
        )

        thresholds = StrictThresholds(alpha_t_threshold=_HLZ_ALPHA_T_THRESHOLD)

        try:
            result = run_bench_strict(
                zoo="crypto_mined",
                universe="crypto",
                period=period,
                random_control=True,
                oos_split=oos_split,
                thresholds=thresholds,
            )
        except Exception as exc:  # noqa: BLE001 — bench failure must not crash the loop
            logger.warning("OverfitGate: run_bench_strict failed: %s", exc)
            return False, {"error": str(exc), "confirmed": False}

        if result.get("status") != "ok":
            return False, {
                "error": result.get("error", "bench returned non-ok status"),
                "confirmed": False,
            }

        # Find the row for our alpha_id.
        rows = result.get("rows", [])
        factor_row: dict[str, Any] | None = None
        for row in rows:
            if row.get("id") == alpha_id:
                factor_row = row
                break

        if factor_row is None:
            logger.warning(
                "OverfitGate: alpha_id %s not found in bench results "
                "(may have been skipped)",
                alpha_id,
            )
            return False, {
                "error": f"alpha_id {alpha_id} not in bench rows",
                "confirmed": False,
                "skipped": result.get("skipped", []),
            }

        category = factor_row.get("_category") or factor_row.get("category", "noise")
        is_confirmed = category == _PASSING_CATEGORY

        details: dict[str, Any] = {
            "confirmed": is_confirmed,
            "category": category,
            "alpha_t_full": factor_row.get("alpha_t_full"),
            "alpha_t_train": factor_row.get("alpha_t_train"),
            "alpha_t_test": factor_row.get("alpha_t_test"),
            "ic_mean": factor_row.get("ic_mean"),
            "ir": factor_row.get("ir"),
            "random_ic_mean": factor_row.get("random_ic_mean"),
            "threshold_alpha_t": _HLZ_ALPHA_T_THRESHOLD,
            "n_alphas_tested": result.get("n_alphas_tested", 0),
            "n_confirmed_alive": result.get("confirmed_alive", 0),
            "oos_split": oos_split,
        }

        if not is_confirmed:
            logger.info(
                "OverfitGate: %s categorised as %s (not confirmed_alive)",
                alpha_id,
                category,
            )

        return is_confirmed, details

    # ------------------------------------------------------------------
    # Combined evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        candidate: FactorCandidate,
        report: BacktestReport,
        oos_split: str | None = None,
        period: str | None = None,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Combined overfit gate evaluation (all four gates).

        Combines the three statistical validation gates
        (:meth:`check`) and the Harvey-Liu-Zhu multi-testing correction
        (:meth:`check_bench_strict`).  A factor passes only if **all**
        gates pass.

        Args:
            candidate: The factor candidate being evaluated.
            report: The backtest report with validation results.
            oos_split: Optional OOS split date for bench_strict.
            period: Data period for bench_strict.  When ``None``,
                derived from the backtest report's run_dir config.json.

        Returns:
            Tuple of ``(passes, reason, details)``:

            * ``passes``: ``True`` only if all gates pass.
            * ``reason``: Human-readable summary of the verdict.
            * ``details``: Combined dict with all gate results.
        """
        alpha_id = candidate.alpha_id
        details: dict[str, Any] = {"alpha_id": alpha_id}

        # Layer 1: Three validation gates.
        if report.status != "ok":
            return (
                False,
                f"backtest status is '{report.status}', not 'ok'",
                {"error": f"backtest status={report.status}", "alpha_id": alpha_id},
            )

        validation_pass, validation_details = self.check(report)
        details["validation_gates"] = validation_details

        if not validation_pass:
            failed_gates = [
                k for k, v in validation_details.items()
                if isinstance(v, dict) and not v.get("pass", True)
            ]
            return (
                False,
                f"validation gates failed: {', '.join(failed_gates)}",
                details,
            )

        # Layer 2: Harvey-Liu-Zhu multi-testing correction.
        if period is None:
            period = self._derive_period_from_report(report)

        if period is None:
            return (
                False,
                "cannot determine data period for bench_strict "
                "(no period provided and config.json unreadable)",
                {**details, "error": "period derivation failed"},
            )

        bench_pass, bench_details = self.check_bench_strict(
            alpha_id, period, oos_split=oos_split,
        )
        details["bench_strict"] = bench_details

        if not bench_pass:
            category = bench_details.get("category", "unknown")
            return (
                False,
                f"bench_strict failed: factor categorised as '{category}' "
                f"(needs 'confirmed_alive')",
                details,
            )

        # All gates passed.
        details["all_pass"] = True
        return (
            True,
            f"all gates passed (3 validation + bench_strict confirmed_alive)",
            details,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_period_from_report(report: BacktestReport) -> str | None:
        """Derive a ``period`` string from the backtest report's config.json.

        Args:
            report: The backtest report with a ``run_dir``.

        Returns:
            Period string like ``"2023-01-01/2024-01-01"``, or ``None``
            if the config cannot be read.
        """
        if not report.run_dir:
            return None
        try:
            config_path = Path(report.run_dir) / "config.json"
            if not config_path.exists():
                return None
            config = json.loads(config_path.read_text(encoding="utf-8"))
            start = config.get("start_date", "")
            end = config.get("end_date", "")
            if start and end:
                return f"{start}/{end}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("OverfitGate: period derivation failed: %s", exc)
        return None
