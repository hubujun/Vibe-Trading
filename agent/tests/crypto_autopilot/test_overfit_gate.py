"""Tests for the three-gate overfitting control + bench_strict integration.

Covers OverfitGate.check() (three statistical validation gates) and
OverfitGate.evaluate() (combined verdict with bench_strict).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from src.crypto_autopilot.overfit_gate import OverfitGate
from src.crypto_autopilot.types import BacktestReport, FactorCandidate

__all__ = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(alpha_id: str = "crypto_mined_test") -> FactorCandidate:
    """Build a minimal FactorCandidate for gate evaluation tests."""
    return FactorCandidate(
        alpha_id=alpha_id,
        source_code="def compute(panel): pass",
        created_at=datetime.now(timezone.utc),
    )


def _make_report(
    validation: dict[str, Any] | None = None,
    *,
    status: str = "ok",
    alpha_id: str = "crypto_mined_test",
) -> BacktestReport:
    """Build a mock BacktestReport with controlled validation dict."""
    return BacktestReport(
        alpha_id=alpha_id,
        run_dir="",
        status=status,
        metrics={},
        validation=validation or {},
        equity_curve=[],
        trades=[],
        passed_gate=False,
        created_at=datetime.now(timezone.utc),
    )


def _passing_validation() -> dict[str, Any]:
    """Validation dict where all three gates pass."""
    return {
        "monte_carlo": {"p_value_sharpe": 0.01},  # < 0.05 → pass
        "bootstrap": {"ci_lower": 0.5},  # > 0 → pass
        "walk_forward": {"consistency_rate": 0.8},  # > 0.6 → pass
    }


# ---------------------------------------------------------------------------
# 1. Three-gate check — OverfitGate.check()
# ---------------------------------------------------------------------------


class TestThreeGateCheck:
    """Verify each of the three statistical validation gates."""

    def test_all_gates_pass(self) -> None:
        report = _make_report(_passing_validation())
        gate = OverfitGate()
        passes, details = gate.check(report)
        assert passes is True
        assert details["all_pass"] is True
        assert details["gate1_monte_carlo"]["pass"] is True
        assert details["gate2_bootstrap"]["pass"] is True
        assert details["gate3_walk_forward"]["pass"] is True

    def test_gate1_fails_when_p_value_gte_threshold(self) -> None:
        validation = _passing_validation()
        validation["monte_carlo"]["p_value_sharpe"] = 0.05  # >= 0.05 → fail
        report = _make_report(validation)
        gate = OverfitGate()
        passes, details = gate.check(report)
        assert passes is False
        assert details["gate1_monte_carlo"]["pass"] is False

    def test_gate2_fails_when_ci_lower_le_zero(self) -> None:
        validation = _passing_validation()
        validation["bootstrap"]["ci_lower"] = 0.0  # <= 0 → fail
        report = _make_report(validation)
        gate = OverfitGate()
        passes, details = gate.check(report)
        assert passes is False
        assert details["gate2_bootstrap"]["pass"] is False

    def test_gate3_fails_when_consistency_le_threshold(self) -> None:
        validation = _passing_validation()
        validation["walk_forward"]["consistency_rate"] = 0.6  # <= 0.6 → fail
        report = _make_report(validation)
        gate = OverfitGate()
        passes, details = gate.check(report)
        assert passes is False
        assert details["gate3_walk_forward"]["pass"] is False

    def test_missing_validation_keys_handled_gracefully(self) -> None:
        """An empty validation dict should not crash — all gates just fail."""
        report = _make_report({})  # no validation keys at all
        gate = OverfitGate()
        passes, details = gate.check(report)
        assert passes is False
        # Each gate's value should be None (missing) → fail.
        assert details["gate1_monte_carlo"]["p_value_sharpe"] is None
        assert details["gate2_bootstrap"]["ci_lower"] is None
        assert details["gate3_walk_forward"]["consistency_rate"] is None


# ---------------------------------------------------------------------------
# 2. Full evaluate — OverfitGate.evaluate()
# ---------------------------------------------------------------------------


class TestEvaluate:
    """Verify the combined overfit gate evaluation."""

    def test_all_gates_pass_and_bench_strict_confirmed(self) -> None:
        report = _make_report(_passing_validation())
        candidate = _make_candidate()
        gate = OverfitGate()

        with patch.object(
            gate,
            "check_bench_strict",
            return_value=(True, {"confirmed": True, "category": "confirmed_alive"}),
        ):
            passes, reason, details = gate.evaluate(
                candidate, report, period="2023-01-01/2024-01-01"
            )

        assert passes is True
        assert "all gates passed" in reason
        assert details["all_pass"] is True
        assert details["bench_strict"]["confirmed"] is True

    def test_any_validation_gate_fails(self) -> None:
        validation = _passing_validation()
        validation["monte_carlo"]["p_value_sharpe"] = 0.10  # Gate 1 fails
        report = _make_report(validation)
        candidate = _make_candidate()
        gate = OverfitGate()

        passes, reason, details = gate.evaluate(
            candidate, report, period="2023-01-01/2024-01-01"
        )

        assert passes is False
        assert "validation gates failed" in reason
        assert "gate1_monte_carlo" in reason

    def test_bench_strict_fails(self) -> None:
        report = _make_report(_passing_validation())
        candidate = _make_candidate()
        gate = OverfitGate()

        with patch.object(
            gate,
            "check_bench_strict",
            return_value=(False, {"confirmed": False, "category": "noise"}),
        ):
            passes, reason, details = gate.evaluate(
                candidate, report, period="2023-01-01/2024-01-01"
            )

        assert passes is False
        assert "bench_strict failed" in reason
        assert details["bench_strict"]["confirmed"] is False

    def test_non_ok_backtest_status_fails_immediately(self) -> None:
        report = _make_report(_passing_validation(), status="error")
        candidate = _make_candidate()
        gate = OverfitGate()

        passes, reason, details = gate.evaluate(
            candidate, report, period="2023-01-01/2024-01-01"
        )

        assert passes is False
        assert "not 'ok'" in reason
