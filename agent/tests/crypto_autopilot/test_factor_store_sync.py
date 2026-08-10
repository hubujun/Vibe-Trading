"""Tests for the FactorStore ↔ SDM strategy-store sync.

Covers artifact registration on ``store()``, status mirroring on
``advance_lifecycle()``, and bench-history recording — the three surfaces
the SDM decay monitor needs to auto-retire decaying autopilot factors.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.crypto_autopilot.factor_store import FactorStore
from src.crypto_autopilot.types import FactorCandidate, FactorLifecycle
from src.strategy_store.models import ArtifactStatus, ArtifactType
from src.strategy_store.store import InMemoryStrategyStore

__all__ = []


def _make_candidate(alpha_id: str = "crypto_mined_test") -> FactorCandidate:
    """Build a minimal factor candidate."""
    return FactorCandidate(
        alpha_id=alpha_id,
        source_code="def compute(panel): pass",
        created_at=datetime.now(timezone.utc),
        meta={"alpha_meta": {"theme": ["momentum"], "decay_horizon": 7}},
    )


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


# ---------------------------------------------------------------------------
# Artifact registration
# ---------------------------------------------------------------------------


class TestArtifactRegistration:
    def test_store_registers_factor_artifact(self, factor_store, sdm_store) -> None:
        """A stored factor becomes a FACTOR/crypto artifact."""
        factor_store.store(_make_candidate())
        artifact = sdm_store.get_artifact("crypto_mined_test")
        assert artifact is not None
        assert artifact.type is ArtifactType.FACTOR
        assert artifact.universe == "crypto"
        assert artifact.status is ArtifactStatus.CREATED
        assert artifact.theme == ("momentum",)
        assert artifact.decay_horizon == 7

    def test_store_is_idempotent(self, factor_store, sdm_store) -> None:
        """Re-storing the same factor never duplicates the artifact."""
        candidate = _make_candidate()
        factor_store.store(candidate)
        factor_store.store(candidate)
        artifacts = sdm_store.list_artifacts()
        assert len(artifacts) == 1

    def test_without_strategy_store_is_noop(self, tmp_path) -> None:
        """No strategy store attached → registration is skipped silently."""
        store = FactorStore(zoo_root=tmp_path / "zoo")
        store.store(_make_candidate())  # should not raise
        assert store.strategy_store is None


# ---------------------------------------------------------------------------
# Lifecycle mirroring
# ---------------------------------------------------------------------------


class TestLifecycleMirror:
    def test_backtested_maps_to_benching(self, factor_store, sdm_store) -> None:
        """BACKTESTED mirrors to the SDM BENCHING status."""
        factor_store.store(_make_candidate())
        factor_store.advance_lifecycle(
            "crypto_mined_test", FactorLifecycle.BACKTESTED,
        )
        assert sdm_store.get_artifact("crypto_mined_test").status is ArtifactStatus.BENCHING

    def test_live_deployed_maps_to_active(self, factor_store, sdm_store) -> None:
        """LIVE_DEPLOYED mirrors to the SDM ACTIVE status."""
        factor_store.store(_make_candidate())
        factor_store.advance_lifecycle(
            "crypto_mined_test", FactorLifecycle.LIVE_DEPLOYED,
        )
        assert sdm_store.get_artifact("crypto_mined_test").status is ArtifactStatus.ACTIVE

    def test_retired_maps_to_disabled(self, factor_store, sdm_store) -> None:
        """RETIRED mirrors to the terminal DISABLED state with a reason."""
        factor_store.store(_make_candidate())
        factor_store.advance_lifecycle(
            "crypto_mined_test", FactorLifecycle.RETIRED,
        )
        artifact = sdm_store.get_artifact("crypto_mined_test")
        assert artifact.status is ArtifactStatus.DISABLED
        assert "retired" in (artifact.disabled_reason or "")

    def test_unmapped_stage_skips_sync(self, factor_store, sdm_store) -> None:
        """DISCOVERED has no SDM mapping — status stays CREATED."""
        factor_store.store(_make_candidate())
        factor_store.advance_lifecycle(
            "crypto_mined_test", FactorLifecycle.DISCOVERED,
        )
        assert sdm_store.get_artifact("crypto_mined_test").status is ArtifactStatus.CREATED

    def test_advance_without_artifact_is_noop(self, factor_store, sdm_store) -> None:
        """Advancing an unregistered factor never crashes."""
        factor_store.advance_lifecycle(
            "crypto_mined_ghost", FactorLifecycle.RETIRED,
        )  # should not raise
        assert sdm_store.get_artifact("crypto_mined_ghost") is None


# ---------------------------------------------------------------------------
# Bench recording
# ---------------------------------------------------------------------------


class TestBenchRecording:
    def test_record_bench_maps_metrics(self, factor_store, sdm_store) -> None:
        """Screen/backtest metrics map into the SDM BenchResult fields."""
        factor_store.record_bench(
            "crypto_mined_test",
            {
                "ic_mean": 0.05,
                "ic_std": 0.1,
                "ic_ir": 0.5,
                "ic_positive_ratio": 0.6,
                "ic_t_stat": 2.1,
                "sharpe": 1.2,
            },
        )
        history = sdm_store.get_bench_history("crypto_mined_test")
        assert len(history) == 1
        bench = history[0]
        assert bench.ic_mean == 0.05
        assert bench.ir == 0.5
        assert bench.ic_positive_ratio == 0.6
        assert bench.t_stat == 2.1
        assert bench.sharpe == 1.2
        assert bench.bench_type == "backtest"

    def test_record_bench_ignores_unknown_keys(self, factor_store, sdm_store) -> None:
        """Extra metric keys are ignored without raising."""
        factor_store.record_bench("crypto_mined_test", {"ic_mean": 0.1, "weird": "x"})
        assert len(sdm_store.get_bench_history("crypto_mined_test")) == 1

    def test_record_bench_without_store_is_noop(self, tmp_path) -> None:
        """No strategy store → bench recording is skipped silently."""
        store = FactorStore(zoo_root=tmp_path / "zoo")
        store.record_bench("crypto_mined_test", {"ic_mean": 0.1})  # no raise
