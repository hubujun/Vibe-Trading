"""Phase 3 tests: factor IC dedup, market regime, weighted sizing,
promotion OOS recheck.

Covers the evolution-loop hardening added in the enterprise roadmap:
correlated-factor rejection, regime labelling, IC-weighted order
notional, and the out-of-sample promotion recheck.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.crypto_autopilot.config import AutopilotConfig
from src.crypto_autopilot.factor_dedup import (
    compute_ic_series,
    dedup_rejection_reason,
    ic_series_correlation,
)
from src.crypto_autopilot.market_regime import classify_regime
from src.crypto_autopilot.types import FactorCandidate, FactorLifecycle

__all__ = []


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _candidate(alpha_id: str, ic_mean: float | None = 0.02) -> FactorCandidate:
    return FactorCandidate(
        alpha_id=alpha_id,
        source_code="def compute(panel): return panel['close']",
        created_at=datetime.now(timezone.utc),
        meta={
            "full_module_source": (
                "import pandas as pd\n"
                "def compute(panel):\n"
                "    return panel['close'].copy()\n"
            ),
            "screen_ic_mean": ic_mean,
        },
    )


def _panel(n: int = 400, n_symbols: int = 4, seed: int = 0) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    close = pd.DataFrame(
        {
            f"S{i}": 100.0 * np.exp(np.cumsum(rng.normal(0.0001, 0.01, n)))
            for i in range(n_symbols)
        },
        index=idx,
    )
    return {"close": close}


def _factor_df(close: pd.DataFrame, *, scale: float = 1.0, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            col: close[col] + rng.normal(0.0, 1.0, len(close)) * scale
            for col in close.columns
        },
        index=close.index,
    )


@pytest.fixture
def orchestrator(runtime_root, monkeypatch):
    """Construct an AutopilotOrchestrator with a temp runtime root."""
    monkeypatch.setattr(
        "src.crypto_autopilot.orchestrator._default_runtime_root",
        lambda: runtime_root,
    )
    from src.crypto_autopilot.orchestrator import AutopilotOrchestrator

    return AutopilotOrchestrator(config=AutopilotConfig())


@pytest.fixture
def runtime_root(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# factor_dedup
# ---------------------------------------------------------------------------


class TestFactorDedup:
    def test_ic_series_length_and_values(self) -> None:
        panel = _panel()
        close = panel["close"]
        factor_df = _factor_df(close)
        ic = compute_ic_series(close, factor_df)
        # Last bar has no forward return → one fewer observation.
        assert len(ic) == len(close) - 1
        assert ic.notna().sum() > len(ic) * 0.9

    def test_identical_series_correlate_perfectly(self) -> None:
        panel = _panel()
        close = panel["close"]
        ic_a = compute_ic_series(close, close)
        ic_b = compute_ic_series(close, close)
        assert ic_series_correlation(ic_a, ic_b) == pytest.approx(1.0)

    def test_independent_series_correlate_near_zero(self) -> None:
        panel = _panel()
        close = panel["close"]
        # scale=200 → factor ≈ pure noise, decorrelated from ``close``;
        # the default scale would leave factor ≈ close and both IC series
        # tracking the same underlying price path.
        ic_a = compute_ic_series(close, _factor_df(close, seed=11, scale=200.0))
        ic_b = compute_ic_series(close, _factor_df(close, seed=22, scale=200.0))
        assert abs(ic_series_correlation(ic_a, ic_b)) < 0.3

    def test_insufficient_overlap_returns_zero(self) -> None:
        a = pd.Series([0.1, 0.2, 0.3], index=[0, 1, 2])
        b = pd.Series([0.1, 0.2, 0.3], index=[0, 1, 2])
        assert ic_series_correlation(a, b) == 0.0

    def test_dedup_rejects_correlated_pair(self) -> None:
        panel = _panel()
        close = panel["close"]
        ic = compute_ic_series(close, close)
        rejected, reason = dedup_rejection_reason("cand_01", ic, [("act_01", ic)])
        assert rejected
        assert "act_01" in reason
        assert "0.7" in reason

    def test_dedup_passes_distinct_pair(self) -> None:
        panel = _panel()
        close = panel["close"]
        ic_a = compute_ic_series(close, _factor_df(close, seed=1, scale=200.0))
        ic_b = compute_ic_series(close, _factor_df(close, seed=2, scale=200.0))
        rejected, reason = dedup_rejection_reason("cand_01", ic_a, [("act_01", ic_b)])
        assert not rejected
        assert reason == ""

    def test_dedup_empty_active_entries_passes(self) -> None:
        panel = _panel()
        close = panel["close"]
        ic = compute_ic_series(close, close)
        rejected, _ = dedup_rejection_reason("cand_01", ic, [])
        assert not rejected


# ---------------------------------------------------------------------------
# market_regime
# ---------------------------------------------------------------------------


def _ar1_close(phi: float, noise_std: float, n: int = 500, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    e = rng.normal(0.0, noise_std, n)
    r = np.zeros(n)
    for i in range(1, n):
        r[i] = phi * r[i - 1] + e[i]
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    base = 100.0 * np.exp(np.cumsum(r))
    # Tiny per-symbol noise: large enough to give the symbols distinct
    # close levels, small enough that the basket return still reflects
    # the shared AR(1) process (a bigger term dominates the lag-1
    # autocorrelation with its own MA(1) noise).
    return pd.DataFrame(
        {f"S{i}": base * (1.0 + 0.001 * rng.normal(size=n)) for i in range(3)},
        index=idx,
    )


class TestMarketRegime:
    def test_positive_autocorrelation_is_trend(self) -> None:
        close = _ar1_close(phi=0.5, noise_std=0.005)
        result = classify_regime(close)
        assert result["regime"] == "trend"
        assert result["lag1_autocorr"] > 0.05

    def test_negative_autocorrelation_is_mean_revert(self) -> None:
        close = _ar1_close(phi=-0.5, noise_std=0.005)
        result = classify_regime(close)
        assert result["regime"] == "mean_revert"
        assert result["lag1_autocorr"] < -0.05

    def test_high_volatility_flag(self) -> None:
        close = _ar1_close(phi=0.0, noise_std=0.02)
        result = classify_regime(close)
        assert result["high_vol"] is True
        assert result["annualized_vol"] > 1.2

    def test_empty_panel_is_unknown(self) -> None:
        result = classify_regime(pd.DataFrame())
        assert result["regime"] == "unknown"

    def test_too_few_bars_is_unknown(self) -> None:
        close = _ar1_close(phi=0.5, noise_std=0.005, n=10)
        result = classify_regime(close)
        assert result["regime"] == "unknown"

    def test_returns_fused_context(self) -> None:
        close = _ar1_close(phi=0.3, noise_std=0.005, n=500)
        result = classify_regime(close)
        # 3 symbols share the same base series → near-perfect correlation.
        assert result["fused"] is True


# ---------------------------------------------------------------------------
# Orchestrator Phase 3 wiring
# ---------------------------------------------------------------------------


class TestWeightedSizing:
    def test_single_factor_gets_full_notional(self, orchestrator) -> None:
        cand = _candidate("alpha_one_01", ic_mean=0.03)
        orchestrator._active_factors.append({
            "alpha_id": cand.alpha_id,
            "lifecycle": FactorLifecycle.BACKTESTED.value,
            "candidate": cand,
        })
        weights = orchestrator._factor_weight_map()
        assert weights == {cand.alpha_id: 1.0}

    def test_two_factors_weighted_by_ic(self, orchestrator) -> None:
        a = _candidate("alpha_a_01", ic_mean=0.01)
        b = _candidate("alpha_b_02", ic_mean=0.03)
        # Generous cap so the raw 1:3 ratio is left untouched — the cap
        # mechanics themselves are covered by test_cap_limits_single_factor_share.
        orchestrator.config = AutopilotConfig(max_single_factor_weight=0.9)
        orchestrator._active_factors.extend([
            {"alpha_id": a.alpha_id, "lifecycle": FactorLifecycle.BACKTESTED.value, "candidate": a},
            {"alpha_id": b.alpha_id, "lifecycle": FactorLifecycle.BACKTESTED.value, "candidate": b},
        ])
        weights = orchestrator._factor_weight_map()
        # b has 3x the |IC| → 3x the weight.
        assert weights[a.alpha_id] < weights[b.alpha_id]
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    def test_cap_limits_single_factor_share(self, orchestrator) -> None:
        orchestrator.config = AutopilotConfig(max_single_factor_weight=0.5)
        a = _candidate("alpha_cap_01", ic_mean=0.001)
        b = _candidate("alpha_dom_02", ic_mean=0.5)
        orchestrator._active_factors.extend([
            {"alpha_id": a.alpha_id, "lifecycle": FactorLifecycle.BACKTESTED.value, "candidate": a},
            {"alpha_id": b.alpha_id, "lifecycle": FactorLifecycle.BACKTESTED.value, "candidate": b},
        ])
        weights = orchestrator._factor_weight_map()
        assert weights[b.alpha_id] <= 0.5 + 1e-9

    def test_four_factors_fall_back_to_equal_weight(self, orchestrator) -> None:
        cands = [_candidate(f"alpha_eq_{i:02d}", ic_mean=0.01 * i) for i in range(4)]
        orchestrator._active_factors.extend([
            {"alpha_id": c.alpha_id, "lifecycle": FactorLifecycle.BACKTESTED.value, "candidate": c}
            for c in cands
        ])
        weights = orchestrator._factor_weight_map()
        assert len(weights) == 4
        assert all(abs(w - 0.25) < 1e-9 for w in weights.values())

    def test_no_factors_returns_empty(self, orchestrator) -> None:
        assert orchestrator._factor_weight_map() == {}


class TestDedupWiring:
    def test_rejects_duplicate_factor(self, orchestrator, monkeypatch) -> None:
        panel = _panel()
        close = panel["close"]
        active = _candidate("active_dup_01")
        candidate = _candidate("cand_dup_01")
        orchestrator._active_factors.append({
            "alpha_id": active.alpha_id,
            "lifecycle": FactorLifecycle.BACKTESTED.value,
            "candidate": active,
        })
        monkeypatch.setattr(
            orchestrator, "_execute_factor",
            lambda c, panel=None: close.copy(),
        )
        rejected, reason = orchestrator._factor_dedup_check(candidate, panel)
        assert rejected
        assert active.alpha_id in reason

    def test_passes_distinct_factor(self, orchestrator, monkeypatch) -> None:
        panel = _panel()
        close = panel["close"]
        active = _candidate("active_ind_01")
        candidate = _candidate("cand_ind_01")
        orchestrator._active_factors.append({
            "alpha_id": active.alpha_id,
            "lifecycle": FactorLifecycle.BACKTESTED.value,
            "candidate": active,
        })
        calls = {"n": 0}

        def _fake_execute(c, panel=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return close.copy()
            return _factor_df(close, seed=99, scale=200.0)

        monkeypatch.setattr(orchestrator, "_execute_factor", _fake_execute)
        rejected, reason = orchestrator._factor_dedup_check(candidate, panel)
        assert not rejected
        assert reason == ""

    def test_missing_panel_passes(self, orchestrator) -> None:
        candidate = _candidate("cand_nop_01")
        rejected, _ = orchestrator._factor_dedup_check(candidate, {})
        assert not rejected


class TestOosRecheck:
    def test_history_unavailable_fails_open(self, orchestrator, monkeypatch) -> None:
        monkeypatch.setattr(
            orchestrator._history, "get_panel",
            lambda *a, **k: None,
        )
        info = {"alpha_id": "paper_hold_01", "candidate": _candidate("paper_hold_01")}
        ok, details = orchestrator._promotion_oos_recheck(info)
        assert ok
        assert details.get("skipped") == "history unavailable"

    def test_backtest_error_fails_closed(self, orchestrator, monkeypatch) -> None:
        monkeypatch.setattr(
            orchestrator._history, "get_panel",
            lambda *a, **k: {"close": _panel()["close"]},
        )
        monkeypatch.setattr(
            orchestrator._backtester, "run_backtest_for_factor",
            lambda c, panel: type(
                "R", (), {"status": "error", "metrics": {"error": "boom"}}
            )(),
        )
        info = {"alpha_id": "paper_err_01", "candidate": _candidate("paper_err_01")}
        ok, details = orchestrator._promotion_oos_recheck(info)
        assert not ok
        assert "boom" in details["reason"]

    def test_gate_failure_keeps_paper(self, orchestrator, monkeypatch) -> None:
        monkeypatch.setattr(
            orchestrator._history, "get_panel",
            lambda *a, **k: {"close": _panel()["close"]},
        )
        monkeypatch.setattr(
            orchestrator._backtester, "run_backtest_for_factor",
            lambda c, panel: type(
                "R", (), {"status": "ok", "metrics": {}}
            )(),
        )
        monkeypatch.setattr(
            orchestrator._overfit_gate, "check",
            lambda report: (False, {
                "gate3_walk_forward": {"consistency_rate": 0.4},
            }),
        )
        info = {"alpha_id": "paper_gate_01", "candidate": _candidate("paper_gate_01")}
        ok, details = orchestrator._promotion_oos_recheck(info)
        assert not ok
        assert "0.4" in details["reason"]

    def test_gate_pass_promotes(self, orchestrator, monkeypatch) -> None:
        monkeypatch.setattr(
            orchestrator._history, "get_panel",
            lambda *a, **k: {"close": _panel()["close"]},
        )
        monkeypatch.setattr(
            orchestrator._backtester, "run_backtest_for_factor",
            lambda c, panel: type(
                "R", (), {"status": "ok", "metrics": {}}
            )(),
        )
        monkeypatch.setattr(
            orchestrator._overfit_gate, "check",
            lambda report: (True, {
                "gate3_walk_forward": {"consistency_rate": 0.9},
            }),
        )
        info = {"alpha_id": "paper_ok_01", "candidate": _candidate("paper_ok_01")}
        ok, details = orchestrator._promotion_oos_recheck(info)
        assert ok
        assert details.get("consistency_rate") == 0.9


class TestRegimeWiring:
    def test_current_regime_unknown_without_panel(self, orchestrator) -> None:
        assert orchestrator._current_regime()["regime"] == "unknown"

    def test_current_regime_from_panel(self, orchestrator) -> None:
        orchestrator._panel = _panel(n=500)
        result = orchestrator._current_regime()
        assert result["regime"] in ("trend", "mean_revert", "mixed")
        assert "high_vol" in result
