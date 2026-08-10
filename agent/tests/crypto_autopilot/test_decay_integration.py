"""Tests for the SDM decay integration — auto-retire of decaying factors.

Covers the full loop: crypto artifacts with degrading bench history flow
through the SDM state machine (active → monitoring → decayed → disabled),
and a disabled artifact retires the autopilot factor (FactorLifecycle →
RETIRED) and emits a ``factor_retired`` IM notification.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.crypto_autopilot.decay_integration import AutopilotDecayManager
from src.crypto_autopilot.factor_store import FactorStore
from src.crypto_autopilot.notifier import AutopilotNotifier
from src.crypto_autopilot.types import FactorCandidate, FactorLifecycle
from src.strategy_store.decay import DecayEvaluator, DecayThresholds
from src.strategy_store.models import Artifact, ArtifactStatus, ArtifactType, BenchResult
from src.strategy_store.store import InMemoryStrategyStore

__all__ = []

#: A CRITICAL-decay bench payload: old IC high, recent IC ~0 (newest last).
_GOOD_IC = 0.1
_BAD_IC = 0.01


def _make_candidate(alpha_id: str = "crypto_mined_test") -> FactorCandidate:
    """Build a minimal factor candidate."""
    return FactorCandidate(
        alpha_id=alpha_id,
        source_code="def compute(panel): pass",
        created_at=datetime.now(timezone.utc),
    )


def _add_decaying_benches(factor_store: FactorStore, alpha_id: str) -> None:
    """Record one batch of benches: 5 good then 5 decaying readings."""
    for _ in range(5):
        factor_store.record_bench(alpha_id, {"ic_mean": _GOOD_IC})
    for _ in range(5):
        factor_store.record_bench(alpha_id, {"ic_mean": _BAD_IC})


@pytest.fixture
def sdm_store() -> InMemoryStrategyStore:
    """An in-memory SDM strategy store."""
    return InMemoryStrategyStore()


@pytest.fixture
def factor_store(tmp_path, sdm_store) -> FactorStore:
    """A FactorStore with a temp zoo and the SDM store attached."""
    return FactorStore(
        zoo_root=tmp_path / "zoo",
        strategy_store=sdm_store,
    )


@pytest.fixture
def manager(tmp_path, factor_store, sdm_store) -> AutopilotDecayManager:
    """A decay manager with a 1-reading state machine and temp outbox."""
    thresholds = DecayThresholds(
        warnings_for_monitoring=1,
        warnings_for_decayed=1,
        critical_for_disabled=1,
    )
    return AutopilotDecayManager(
        factor_store=factor_store,
        strategy_store=sdm_store,
        notifier=AutopilotNotifier(tmp_path),
        evaluator=DecayEvaluator(thresholds),
    )


def _register_active_factor(factor_store, sdm_store, alpha_id: str = "crypto_mined_test") -> None:
    """Register a factor and promote it to the SDM ACTIVE state."""
    factor_store.store(_make_candidate(alpha_id))
    factor_store.advance_lifecycle(alpha_id, FactorLifecycle.LIVE_DEPLOYED)
    assert sdm_store.get_artifact(alpha_id).status is ArtifactStatus.ACTIVE


# ---------------------------------------------------------------------------
# Full decay → retire loop
# ---------------------------------------------------------------------------


class TestDecayRetireLoop:
    def test_decayed_factor_is_retired_and_notified(
        self, tmp_path, manager, factor_store, sdm_store,
    ) -> None:
        """A factor that decays to DISABLED is retired + notified."""
        _register_active_factor(factor_store, sdm_store)

        # Three scans with degrading benches: active→monitoring→decayed→disabled.
        for _ in range(3):
            _add_decaying_benches(factor_store, "crypto_mined_test")
            manager.run_scan()

        artifact = sdm_store.get_artifact("crypto_mined_test")
        assert artifact.status is ArtifactStatus.DISABLED

        # The IM outbox carries exactly one factor_retired notification.
        outbox = list((tmp_path / "notifications").glob("*.json"))
        assert len(outbox) == 1
        payload = json.loads(outbox[0].read_text(encoding="utf-8"))
        assert payload["kind"] == "factor_retired"
        assert payload["meta"]["alpha_id"] == "crypto_mined_test"

    def test_scan_summary_reports_retired(self, manager, factor_store, sdm_store) -> None:
        """The scan that disables the factor lists it as retired."""
        _register_active_factor(factor_store, sdm_store)
        summary: dict | None = None
        for _ in range(3):
            _add_decaying_benches(factor_store, "crypto_mined_test")
            summary = manager.run_scan()
        assert summary is not None
        assert len(summary["retired"]) == 1
        assert summary["retired"][0]["artifact_id"] == "crypto_mined_test"
        assert summary["signals"].get("critical", 0) >= 1
        assert summary["transitions"]  # transitions were applied

    def test_dry_run_never_retires(self, manager, factor_store, sdm_store) -> None:
        """dry_run reports the verdict without retiring anything."""
        _register_active_factor(factor_store, sdm_store)
        for _ in range(3):
            _add_decaying_benches(factor_store, "crypto_mined_test")
        summary = manager.run_scan(dry_run=True)
        assert summary["signals"].get("critical", 0) >= 1
        assert summary["retired"] == []
        assert sdm_store.get_artifact("crypto_mined_test").status is ArtifactStatus.ACTIVE

    def test_healthy_factor_is_never_retired(
        self, manager, factor_store, sdm_store,
    ) -> None:
        """Stable IC keeps the factor active through repeated scans."""
        _register_active_factor(factor_store, sdm_store)
        for _ in range(4):
            factor_store.record_bench("crypto_mined_test", {"ic_mean": 0.1})
        for _ in range(4):
            summary = manager.run_scan()
        assert summary["retired"] == []
        assert sdm_store.get_artifact("crypto_mined_test").status is ArtifactStatus.ACTIVE


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_insufficient_bench_is_skipped(self, manager, factor_store, sdm_store) -> None:
        """Fewer than 3 bench readings → counted as insufficient data."""
        _register_active_factor(factor_store, sdm_store)
        factor_store.record_bench("crypto_mined_test", {"ic_mean": 0.1})
        summary = manager.run_scan()
        assert summary["insufficient_data"] == 1
        assert summary["scanned"] == 1
        assert summary["retired"] == []

    def test_non_crypto_artifacts_are_ignored(
        self, manager, factor_store, sdm_store,
    ) -> None:
        """Only the crypto universe participates in the autopilot scan."""
        sdm_store.register_artifact(
            Artifact(
                id="equity_alpha", type=ArtifactType.FACTOR,
                name="equity_alpha", universe="equity",
                status=ArtifactStatus.ACTIVE,
            )
        )
        sdm_store.record_bench(
            BenchResult(artifact_id="equity_alpha", ic_mean=0.01),
        )
        summary = manager.run_scan()
        assert summary["scanned"] == 0
        assert sdm_store.get_artifact("equity_alpha").status is ArtifactStatus.ACTIVE

    def test_scan_without_store_is_noop(self, manager, factor_store, monkeypatch) -> None:
        """An unavailable strategy store degrades to a no-op scan."""
        manager._strategy_store = None

        def _boom() -> None:
            raise RuntimeError("store unavailable")

        monkeypatch.setattr("src.strategy_store._shared.get_store", _boom)
        summary = manager.run_scan()
        assert summary == {
            "scanned": 0, "signals": {}, "retired": [], "transitions": [], "insufficient_data": 0,
        }

    def test_retire_factor_directly(self, tmp_path, manager, factor_store, monkeypatch) -> None:
        """retire_factor() retires + notifies in one step."""
        calls: list[tuple] = []

        def _spy(alpha_id, stage):
            calls.append((alpha_id, stage))

        monkeypatch.setattr(factor_store, "advance_lifecycle", _spy)
        manager.retire_factor("crypto_mined_test", "manual gate verdict")
        assert calls == [("crypto_mined_test", FactorLifecycle.RETIRED)]
        files = list((tmp_path / "notifications").glob("*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["kind"] == "factor_retired"
