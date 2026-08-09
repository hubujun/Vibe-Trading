"""Tests for the 24/7 autopilot orchestrator.

Covers state machine transitions, crash recovery via HealthMonitor,
tick safety (_safe_tick error isolation), and status reporting.

External dependencies (OKX SDK, LLM, etc.) are never called — the
constructor is exercised but tick methods that would hit the network
are not invoked.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.crypto_autopilot.config import AutopilotConfig
from src.crypto_autopilot.health import HealthMonitor
from src.crypto_autopilot.types import PipelinePhase, PipelineState

__all__ = []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime_root(tmp_path):
    """A temp directory used as the orchestrator's runtime root."""
    return tmp_path


@pytest.fixture
def orchestrator(runtime_root, monkeypatch):
    """Construct an AutopilotOrchestrator with a temp runtime root.

    Monkeypatches ``_default_runtime_root`` so HealthMonitor reads/writes
    state files under ``tmp_path`` rather than the real ``runs/`` directory.
    """
    monkeypatch.setattr(
        "src.crypto_autopilot.orchestrator._default_runtime_root",
        lambda: runtime_root,
    )
    from src.crypto_autopilot.orchestrator import AutopilotOrchestrator

    config = AutopilotConfig()
    return AutopilotOrchestrator(config=config)


# ---------------------------------------------------------------------------
# 1. State machine transitions
# ---------------------------------------------------------------------------


class TestStateMachine:
    """Verify initial state and legal phase transitions."""

    def test_initial_state_is_idle(self, orchestrator) -> None:
        assert orchestrator.pipeline_state.phase == PipelinePhase.IDLE

    def test_get_status_returns_correct_phase(self, orchestrator) -> None:
        status = orchestrator.get_status()
        assert status["pipeline_phase"] == PipelinePhase.IDLE.value

    def test_get_status_returns_zero_tick_count(self, orchestrator) -> None:
        status = orchestrator.get_status()
        assert status["tick_count"] == 0

    def test_pipeline_transitions_through_legal_phases(self, orchestrator) -> None:
        """_set_phase updates the phase on the pipeline state snapshot."""
        for phase in [
            PipelinePhase.COLLECTING,
            PipelinePhase.DISCOVERING,
            PipelinePhase.BACKTESTING,
            PipelinePhase.PAPER_TRADING,
            PipelinePhase.FEEDBACK,
        ]:
            orchestrator._set_phase(phase)
            assert orchestrator.pipeline_state.phase == phase


# ---------------------------------------------------------------------------
# 2. Crash recovery
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    """Verify HealthMonitor state persistence and orchestrator resume."""

    def test_health_monitor_save_and_load(self, runtime_root) -> None:
        """save_pipeline_state persists; load_pipeline_state restores."""
        health = HealthMonitor(runtime_root)

        saved = PipelineState(
            phase=PipelinePhase.BACKTESTING,
            active_factor_id="crypto_mined_test",
            tick_count=5,
            last_tick_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        health.save_pipeline_state(saved)

        loaded = health.load_pipeline_state()
        assert loaded is not None
        assert loaded.phase == PipelinePhase.BACKTESTING
        assert loaded.tick_count == 5
        assert loaded.active_factor_id == "crypto_mined_test"

    def test_health_monitor_load_returns_none_when_no_state(self, runtime_root) -> None:
        """No state file → load returns None (start fresh)."""
        health = HealthMonitor(runtime_root)
        assert health.load_pipeline_state() is None

    def test_orchestrator_resumes_from_saved_state(
        self, runtime_root, monkeypatch,
    ) -> None:
        """A new orchestrator instance loads persisted state, not IDLE."""
        monkeypatch.setattr(
            "src.crypto_autopilot.orchestrator._default_runtime_root",
            lambda: runtime_root,
        )

        # Pre-populate state file (simulates a prior crash mid-run).
        health = HealthMonitor(runtime_root)
        saved = PipelineState(
            phase=PipelinePhase.BACKTESTING,
            tick_count=5,
            last_tick_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        health.save_pipeline_state(saved)

        # Construct a fresh orchestrator (simulates restart after crash).
        from src.crypto_autopilot.orchestrator import AutopilotOrchestrator

        orch = AutopilotOrchestrator(config=AutopilotConfig())

        assert orch.pipeline_state.phase == PipelinePhase.BACKTESTING
        assert orch.pipeline_state.tick_count == 5

    def test_orchestrator_starts_idle_when_no_saved_state(
        self, runtime_root, monkeypatch,
    ) -> None:
        """No persisted state → orchestrator starts at IDLE with 0 ticks."""
        monkeypatch.setattr(
            "src.crypto_autopilot.orchestrator._default_runtime_root",
            lambda: runtime_root,
        )
        from src.crypto_autopilot.orchestrator import AutopilotOrchestrator

        orch = AutopilotOrchestrator(config=AutopilotConfig())
        assert orch.pipeline_state.phase == PipelinePhase.IDLE
        assert orch.pipeline_state.tick_count == 0


# ---------------------------------------------------------------------------
# 3. Tick safety
# ---------------------------------------------------------------------------


class TestTickSafety:
    """Verify _safe_tick isolates exceptions and still increments counters."""

    def test_safe_tick_catches_exception(self, orchestrator) -> None:
        """A tick that raises is caught; tick_count still increments."""
        initial_count = orchestrator.pipeline_state.tick_count

        async def boom():
            raise RuntimeError("tick crash")

        asyncio.run(orchestrator._safe_tick("test", boom))

        assert orchestrator.pipeline_state.tick_count == initial_count + 1

    def test_safe_tick_successful_increments_count(self, orchestrator) -> None:
        """A successful tick increments tick_count."""
        initial_count = orchestrator.pipeline_state.tick_count

        async def ok_tick():
            pass

        asyncio.run(orchestrator._safe_tick("test", ok_tick))

        assert orchestrator.pipeline_state.tick_count == initial_count + 1

    def test_safe_tick_updates_last_tick_at(self, orchestrator) -> None:
        """After a tick, last_tick_at is set to a recent timestamp."""
        async def ok_tick():
            pass

        before = datetime.now(timezone.utc)
        asyncio.run(orchestrator._safe_tick("test", ok_tick))

        assert orchestrator.pipeline_state.last_tick_at is not None
        assert orchestrator.pipeline_state.last_tick_at >= before


# ---------------------------------------------------------------------------
# 4. Status reporting
# ---------------------------------------------------------------------------


class TestStatusReporting:
    """Verify get_status() returns all expected keys with correct types."""

    def test_get_status_includes_expected_keys(self, orchestrator) -> None:
        status = orchestrator.get_status()
        expected_keys = {
            "pipeline_phase",
            "tick_count",
            "last_tick_at",
            "active_factors",
            "pending_candidates",
            "active_factor_ids",
            "health_alive",
            "health_stale",
            "memory",
            "mining_hints",
            "risk_halted",
            "config",
        }
        assert expected_keys.issubset(status.keys())

    def test_get_status_risk_halted_is_false_initially(self, orchestrator) -> None:
        status = orchestrator.get_status()
        assert status["risk_halted"] is False

    def test_get_status_active_factors_is_zero_initially(self, orchestrator) -> None:
        status = orchestrator.get_status()
        assert status["active_factors"] == 0
        assert status["pending_candidates"] == 0

    def test_get_status_config_reflects_autopilot_config(self, orchestrator) -> None:
        status = orchestrator.get_status()
        cfg = status["config"]
        assert "pairs" in cfg
        assert "mine_interval_hours" in cfg
        assert "trade_interval_minutes" in cfg
