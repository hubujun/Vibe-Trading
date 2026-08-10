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

    def test_health_monitor_persists_regime_in_state(self, runtime_root) -> None:
        """Phase 3: the market-regime snapshot round-trips through state.json."""
        health = HealthMonitor(runtime_root)

        saved = PipelineState(
            phase=PipelinePhase.FEEDBACK,
            tick_count=7,
            last_tick_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        health.save_pipeline_state(saved, regime={
            "regime": "trend", "high_vol": False, "fused": None,
        })

        loaded = health.load_pipeline_state()
        assert loaded is not None
        assert loaded.regime == {"regime": "trend", "high_vol": False, "fused": None}

    def test_health_monitor_loads_legacy_state_without_regime(
        self, runtime_root,
    ) -> None:
        """A pre-Phase-3 state.json (no regime key) still loads."""
        import json as _json

        health = HealthMonitor(runtime_root)
        saved = PipelineState(
            phase=PipelinePhase.PAPER_TRADING,
            tick_count=3,
            last_tick_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        health.save_pipeline_state(saved)
        # Strip the regime key — simulating a legacy payload.
        path = runtime_root / "autopilot" / "state.json"
        raw = _json.loads(path.read_text(encoding="utf-8"))
        raw.pop("regime", None)
        path.write_text(_json.dumps(raw), encoding="utf-8")

        loaded = health.load_pipeline_state()
        assert loaded is not None
        assert loaded.phase == PipelinePhase.PAPER_TRADING
        assert loaded.regime is None

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


# ---------------------------------------------------------------------------
# 3. Trade gating (signal + cooldown)
# ---------------------------------------------------------------------------


class TestTradeGating:
    """Verify paper orders are gated by factor signal and cooldown."""

    @staticmethod
    def _active_factor(ic_mean: float = 0.03) -> dict:
        from src.crypto_autopilot.types import FactorCandidate, FactorLifecycle

        candidate = FactorCandidate(
            alpha_id="test_momentum_gate",
            source_code="",
            created_at=datetime.now(timezone.utc),
            meta={"screen_ic_mean": ic_mean},
        )
        return {
            "alpha_id": candidate.alpha_id,
            "lifecycle": FactorLifecycle.BACKTESTED.value,
            "candidate": candidate,
        }

    @staticmethod
    def _orchestrator_with_factor(orchestrator, monkeypatch, values, ic_mean=0.03):
        """Inject one active factor; return (orchestrator, mocked engine)."""
        import pandas as pd
        from unittest.mock import MagicMock

        orch = orchestrator
        orch._active_factors = [TestTradeGating._active_factor(ic_mean=ic_mean)]

        def fake_execute(candidate):
            return pd.DataFrame({"BTC-USDT": values})

        monkeypatch.setattr(orch, "_execute_factor", fake_execute)
        engine = MagicMock()
        engine.place_order.return_value = {"status": "ok"}
        monkeypatch.setattr(orch, "_paper_engine", engine)
        return orch, engine

    def test_positive_signal_fires_order(self, orchestrator, monkeypatch) -> None:
        orch, engine = self._orchestrator_with_factor(
            orchestrator, monkeypatch, [0.5, 0.8],
        )
        asyncio.run(orch._tick_trade())
        engine.place_order.assert_called_once()
        assert orch._last_trade_ts > 0.0

    def test_flat_latest_value_skips_order(self, orchestrator, monkeypatch) -> None:
        orch, engine = self._orchestrator_with_factor(
            orchestrator, monkeypatch, [0.5, -0.3],
        )
        asyncio.run(orch._tick_trade())
        engine.place_order.assert_not_called()

    def test_negative_ic_trades_inverse(self, orchestrator, monkeypatch) -> None:
        # Screened with negative IC → latest negative value is the signal.
        orch, engine = self._orchestrator_with_factor(
            orchestrator, monkeypatch, [-0.2, -0.6], ic_mean=-0.03,
        )
        asyncio.run(orch._tick_trade())
        engine.place_order.assert_called_once()

        orch2, engine2 = self._orchestrator_with_factor(
            orchestrator, monkeypatch, [0.2, 0.6], ic_mean=-0.03,
        )
        asyncio.run(orch2._tick_trade())
        engine2.place_order.assert_not_called()

    def test_cooldown_blocks_repeat_order(self, orchestrator, monkeypatch) -> None:
        import time

        orch, engine = self._orchestrator_with_factor(
            orchestrator, monkeypatch, [0.5, 0.8],
        )
        orch._last_trade_ts = time.monotonic()
        asyncio.run(orch._tick_trade())
        engine.place_order.assert_not_called()

    def test_cooldown_expired_allows_order(self, orchestrator, monkeypatch) -> None:
        import time

        orch, engine = self._orchestrator_with_factor(
            orchestrator, monkeypatch, [0.5, 0.8],
        )
        cooldown_s = orch.config.trade_cooldown_minutes * 60
        orch._last_trade_ts = time.monotonic() - cooldown_s - 1.0
        asyncio.run(orch._tick_trade())
        engine.place_order.assert_called_once()

    def test_mine_stores_screen_ic_direction(self, orchestrator, monkeypatch) -> None:
        import pandas as pd
        from src.crypto_autopilot.types import FactorCandidate, FactorLifecycle

        orch = orchestrator
        orch._panel = {
            "close": pd.DataFrame({"BTC-USDT": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]}),
        }
        candidate = FactorCandidate(
            alpha_id="test_ic_direction_store",
            source_code="",
            created_at=datetime.now(timezone.utc),
            lifecycle=FactorLifecycle.DISCOVERED,
        )
        monkeypatch.setattr(
            orch._factor_miner, "mine_factors", lambda **kw: [candidate],
        )
        monkeypatch.setattr(
            orch, "_execute_factor",
            lambda cand: pd.DataFrame({"BTC-USDT": [0.1, 0.2]}),
        )
        monkeypatch.setattr(
            orch._factor_screen, "screen",
            lambda factor_df, return_df: {"pass_screen": True, "ic_mean": 0.042},
        )
        monkeypatch.setattr(orch._factor_store, "store", lambda cand: None)

        asyncio.run(orch._tick_mine())
        assert candidate.meta.get("screen_ic_mean") == 0.042
        assert orch._pending_candidates == [candidate]


# ---------------------------------------------------------------------------
# 4. Position management (take-profit / stop-loss / max holding)
# ---------------------------------------------------------------------------


class TestPositionManagement:
    """Open positions are closed on TP/SL/holding triggers."""

    @staticmethod
    def _orchestrator_with_positions(orchestrator, monkeypatch, positions):
        from unittest.mock import MagicMock
        from src.crypto_autopilot.types import PaperPosition

        orch = orchestrator
        engine = MagicMock()
        engine.get_positions.return_value = positions
        engine.close_position.return_value = {"status": "ok"}
        monkeypatch.setattr(orch, "_paper_engine", engine)
        return orch, engine

    def _position(self, pnl: float, entry_hours_ago: float = 1.0) -> PaperPosition:
        from datetime import timedelta
        from src.crypto_autopilot.types import PaperPosition

        return PaperPosition(
            symbol="BTC-USDT",
            side="long",
            quantity=0.5,
            entry_price=100.0,
            entry_time=datetime.now(timezone.utc) - timedelta(hours=entry_hours_ago),
            unrealized_pnl=pnl,
        )

    def test_take_profit_closes_position(self, orchestrator, monkeypatch) -> None:
        orch, engine = self._orchestrator_with_positions(
            orchestrator, monkeypatch, [self._position(pnl=6.0)],
        )
        orch._manage_positions()
        engine.close_position.assert_called_once_with("BTC-USDT")

    def test_stop_loss_closes_position(self, orchestrator, monkeypatch) -> None:
        orch, engine = self._orchestrator_with_positions(
            orchestrator, monkeypatch, [self._position(pnl=-6.0)],
        )
        orch._manage_positions()
        engine.close_position.assert_called_once_with("BTC-USDT")

    def test_max_holding_closes_position(self, orchestrator, monkeypatch) -> None:
        orch, engine = self._orchestrator_with_positions(
            orchestrator, monkeypatch,
            [self._position(pnl=0.0, entry_hours_ago=25.0)],
        )
        orch._manage_positions()
        engine.close_position.assert_called_once_with("BTC-USDT")

    def test_within_bands_keeps_position(self, orchestrator, monkeypatch) -> None:
        orch, engine = self._orchestrator_with_positions(
            orchestrator, monkeypatch,
            [self._position(pnl=1.0, entry_hours_ago=2.0)],
        )
        orch._manage_positions()
        engine.close_position.assert_not_called()

    def test_manage_positions_runs_before_factor_gate(self, orchestrator, monkeypatch) -> None:
        """Position exits fire even without active factors or signals."""
        from unittest.mock import MagicMock

        orch, engine = self._orchestrator_with_positions(
            orchestrator, monkeypatch, [self._position(pnl=-6.0)],
        )
        orch._active_factors = []
        asyncio.run(orch._tick_trade())
        engine.close_position.assert_called_once_with("BTC-USDT")


# ---------------------------------------------------------------------------
# 6. Evaluation window (Phase 1: long history for statistical gates)
# ---------------------------------------------------------------------------


class TestEvaluateWindow:
    """Verify _tick_evaluate prefers the long history-store window."""

    @staticmethod
    def _pending_candidate(alpha_id: str = "cand_win_01"):
        from src.crypto_autopilot.types import FactorCandidate

        return FactorCandidate(
            alpha_id=alpha_id,
            source_code="def compute(panel): return panel['close']",
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _panel(n: int = 400, n_symbols: int = 4) -> dict:
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(0)
        idx = pd.date_range("2026-01-01", periods=n, freq="h")
        close = pd.DataFrame(
            {
                f"S{i}": 100.0 * np.exp(np.cumsum(rng.normal(0.0001, 0.01, n)))
                for i in range(n_symbols)
            },
            index=idx,
        )
        return {"close": close}

    def test_uses_history_store_window_and_logs_it(
        self, orchestrator, monkeypatch, caplog,
    ) -> None:
        """With history available the eval window log names the bar count."""
        import logging

        panel = self._panel()
        orchestrator._pending_candidates = [self._pending_candidate()]
        monkeypatch.setattr(
            orchestrator._history, "get_panel",
            lambda *a, **k: panel,
        )
        monkeypatch.setattr(
            orchestrator._backtester, "run_backtest_for_factor",
            lambda c, p: type(
                "R", (), {"status": "ok", "metrics": {}}
            )(),
        )
        monkeypatch.setattr(
            orchestrator._overfit_gate, "evaluate",
            lambda c, r: (True, "ok", {}),
        )
        with caplog.at_level(logging.INFO, logger="src.crypto_autopilot.orchestrator"):
            asyncio.run(orchestrator._tick_evaluate())
        assert any(
            "eval window 1440 bars" in r.message for r in caplog.records
        )

    def test_falls_back_to_live_panel_when_history_empty(
        self, orchestrator, monkeypatch, caplog,
    ) -> None:
        """An empty history store degrades to the rolling live panel."""
        import logging

        panel = self._panel()
        orchestrator._pending_candidates = [self._pending_candidate()]
        orchestrator._panel = panel
        monkeypatch.setattr(
            orchestrator._history, "get_panel",
            lambda *a, **k: {},
        )
        monkeypatch.setattr(
            orchestrator._backtester, "run_backtest_for_factor",
            lambda c, p: type(
                "R", (), {"status": "ok", "metrics": {}}
            )(),
        )
        monkeypatch.setattr(
            orchestrator._overfit_gate, "evaluate",
            lambda c, r: (True, "ok", {}),
        )
        with caplog.at_level(logging.WARNING, logger="src.crypto_autopilot.orchestrator"):
            asyncio.run(orchestrator._tick_evaluate())
        assert any(
            "history store empty; falling back to live panel" in r.message
            for r in caplog.records
        )
