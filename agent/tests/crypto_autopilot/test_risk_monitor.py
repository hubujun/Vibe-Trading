"""Tests for the autopilot risk monitor with auto-halt.

Covers the daily loss circuit breaker, consecutive loss detection,
the halt mechanism (trigger / is_halted / clear), and the full
evaluate() entry point.

The HALT sentinel path is redirected to a temp directory via the
``VIBE_TRADING_HOME`` env var so tests are fully isolated.
"""

from __future__ import annotations

import pytest

from src.crypto_autopilot.config import AutopilotConfig
from src.crypto_autopilot.risk_monitor import RiskMonitor

__all__ = []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def risk_monitor(monkeypatch, tmp_path):
    """A RiskMonitor whose HALT sentinel resolves under ``tmp_path``.

    ``src.live.halt`` resolves the sentinel path via
    ``src.config.paths.get_runtime_root()`` which reads the
    ``VIBE_TRADING_HOME`` env var.  Setting it to ``tmp_path`` ensures
    HALT files are created and cleaned up inside the temp dir. The
    IM-notify outbox is redirected to ``tmp_path`` as well so halt
    notifications never leak into the real autopilot runtime tree.
    """
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path))
    # Redirect the IM-notify outbox so halt notifications stay in the
    # temp dir too (the notifier resolves its root via _default_runtime_root).
    monkeypatch.setattr(
        "src.crypto_autopilot.risk_monitor._default_runtime_root",
        lambda: tmp_path,
    )
    # Ensure any prior halt sentinel from a previous test is cleared.
    monitor = RiskMonitor(config=AutopilotConfig())
    monitor.clear_halt()
    return monitor


# ---------------------------------------------------------------------------
# 1. Daily loss circuit breaker
# ---------------------------------------------------------------------------


class TestDailyLoss:
    """Verify the daily drawdown check against the kill-loss threshold."""

    def test_six_percent_loss_triggers_halt(self, risk_monitor: RiskMonitor) -> None:
        """6% loss exceeds the 5% kill threshold → halt triggered."""
        halt, loss_pct = risk_monitor.check_daily_loss(
            current_equity=94.0, start_of_day_equity=100.0,
        )
        assert halt is True
        assert loss_pct == pytest.approx(6.0)
        assert risk_monitor.is_halted()

    def test_three_percent_loss_no_halt(self, risk_monitor: RiskMonitor) -> None:
        """3% loss is below the 5% threshold → no halt."""
        halt, loss_pct = risk_monitor.check_daily_loss(
            current_equity=97.0, start_of_day_equity=100.0,
        )
        assert halt is False
        assert loss_pct == pytest.approx(3.0)
        assert not risk_monitor.is_halted()

    def test_zero_loss_no_halt(self, risk_monitor: RiskMonitor) -> None:
        """0% loss (no drawdown) → no halt."""
        halt, loss_pct = risk_monitor.check_daily_loss(
            current_equity=100.0, start_of_day_equity=100.0,
        )
        assert halt is False
        assert loss_pct == pytest.approx(0.0)
        assert not risk_monitor.is_halted()


# ---------------------------------------------------------------------------
# 2. Consecutive loss detection
# ---------------------------------------------------------------------------


class TestConsecutiveLosses:
    """Verify the trailing negative-day streak check."""

    def test_three_negative_days_triggers_halt(self, risk_monitor: RiskMonitor) -> None:
        """Last 3 entries all negative → halt."""
        halt, streak = risk_monitor.check_consecutive_losses([-1.0, -2.0, -3.0])
        assert halt is True
        assert streak == 3
        assert risk_monitor.is_halted()

    def test_two_negative_days_no_halt(self, risk_monitor: RiskMonitor) -> None:
        """Only 2 trailing negatives (< threshold of 3) → no halt."""
        halt, streak = risk_monitor.check_consecutive_losses([1.0, -1.0, -2.0])
        assert halt is False
        assert streak == 2
        assert not risk_monitor.is_halted()

    def test_mixed_pnls_no_halt(self, risk_monitor: RiskMonitor) -> None:
        """[-1, +2, -3] has only 1 trailing negative → no halt."""
        halt, streak = risk_monitor.check_consecutive_losses([-1.0, 2.0, -3.0])
        assert halt is False
        assert streak == 1
        assert not risk_monitor.is_halted()

    def test_empty_list_no_halt(self, risk_monitor: RiskMonitor) -> None:
        """An empty or short list → no halt."""
        halt, streak = risk_monitor.check_consecutive_losses([])
        assert halt is False
        assert streak == 0


# ---------------------------------------------------------------------------
# 3. Halt mechanism
# ---------------------------------------------------------------------------


class TestHaltMechanism:
    """Verify trigger_halt / is_halted / clear_halt lifecycle."""

    def test_trigger_halt_creates_sentinel(self, risk_monitor: RiskMonitor) -> None:
        """trigger_halt writes the HALT file."""
        assert not risk_monitor.is_halted()
        risk_monitor.trigger_halt("test reason")
        assert risk_monitor.is_halted()

    def test_is_halted_true_after_trigger(self, risk_monitor: RiskMonitor) -> None:
        risk_monitor.trigger_halt("halt test")
        assert risk_monitor.is_halted() is True

    def test_clear_halt_removes_sentinel(self, risk_monitor: RiskMonitor) -> None:
        """clear_halt deletes the HALT file."""
        risk_monitor.trigger_halt("temp halt")
        assert risk_monitor.is_halted()

        risk_monitor.clear_halt()
        assert not risk_monitor.is_halted()

    def test_is_halted_false_after_clear(self, risk_monitor: RiskMonitor) -> None:
        risk_monitor.trigger_halt("will clear")
        risk_monitor.clear_halt()
        assert risk_monitor.is_halted() is False

    def test_clear_halt_when_not_halted_is_noop(self, risk_monitor: RiskMonitor) -> None:
        """Clearing when no sentinel exists is a safe no-op."""
        risk_monitor.clear_halt()  # should not raise
        assert not risk_monitor.is_halted()


# ---------------------------------------------------------------------------
# 4. Full evaluate
# ---------------------------------------------------------------------------


class TestFullEvaluate:
    """Verify the combined evaluate() entry point."""

    def test_six_percent_loss_and_three_consecutive_halt(
        self, risk_monitor: RiskMonitor,
    ) -> None:
        """Both conditions met → halt_triggered=True with both reasons."""
        result = risk_monitor.evaluate(
            current_equity=94.0,
            start_of_day_equity=100.0,
            daily_pnls=[-1.0, -2.0, -3.0],
        )
        assert result["halt_triggered"] is True
        assert result["daily_loss_pct"] == pytest.approx(6.0)
        assert result["consecutive_losses"] == 3
        assert result["reason"] != ""

    def test_no_loss_no_consecutive_no_halt(
        self, risk_monitor: RiskMonitor,
    ) -> None:
        """No loss, no consecutive negatives → halt_triggered=False."""
        result = risk_monitor.evaluate(
            current_equity=100.0,
            start_of_day_equity=100.0,
            daily_pnls=[1.0, 2.0, 3.0],
        )
        assert result["halt_triggered"] is False
        assert result["daily_loss_pct"] == pytest.approx(0.0)
        assert result["consecutive_losses"] == 0
        assert result["reason"] == ""

    def test_returns_all_expected_keys(self, risk_monitor: RiskMonitor) -> None:
        """The evaluate dict must include all expected status keys."""
        result = risk_monitor.evaluate(
            current_equity=100.0,
            start_of_day_equity=100.0,
            daily_pnls=[],
        )
        expected_keys = {
            "daily_loss_pct",
            "halt_triggered",
            "consecutive_losses",
            "reason",
            "ts",
        }
        assert expected_keys.issubset(result.keys())

    def test_already_halted_short_circuits(self, risk_monitor: RiskMonitor) -> None:
        """When HALT is already active, evaluate short-circuits."""
        risk_monitor.trigger_halt("pre-existing halt")
        result = risk_monitor.evaluate(
            current_equity=100.0,
            start_of_day_equity=100.0,
            daily_pnls=[],
        )
        assert result["halt_triggered"] is True
        assert "already active" in result["reason"]
