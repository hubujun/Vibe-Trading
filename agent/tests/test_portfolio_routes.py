"""Tests for the Portfolio Studio HTTP routes.

All four endpoints are pure computations over caller-supplied data, so no
network or artifact mocking is needed — fixtures are deterministic random
panels built with fixed seeds. The engine layers are covered by their own
suites; these tests pin the HTTP contract: status codes, envelope shape,
and error mapping (ValueError → 400, empty/bad input → 400).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

import api_server

__all__ = []


# ---------------------------------------------------------------------------
# Deterministic fixtures
# ---------------------------------------------------------------------------


def _price_panel(n: int = 60, seed: int = 7) -> dict:
    """Three correlated-ish geometric random walks, as symbol → price lists."""
    rng = np.random.default_rng(seed)
    panel: dict = {}
    for sym, start in (("AAA", 100.0), ("BBB", 80.0), ("CCC", 120.0)):
        rets = rng.normal(0.0005, 0.012, n)
        panel[sym] = [round(float(v), 6) for v in start * np.exp(np.cumsum(rets))]
    return panel


XRAY_WEIGHTS = {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2}


def _returns_panel(n: int = 70, seed: int = 11, start: str = "2024-09-02") -> dict:
    """date → symbol → return, aligned on business days."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d")
    frame: dict = {}
    for dt in dates:
        frame[str(dt)] = {
            sym: round(float(rng.normal(0.0005, 0.012)), 6)
            for sym in ("AAA", "BBB", "CCC")
        }
    return frame


def _positions_panel(n: int = 70, start: str = "2024-09-02") -> dict:
    """date → symbol → raw signal position (signed weights)."""
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d")
    return {str(dt): {"AAA": 1.0, "BBB": 0.5, "CCC": 0.25} for dt in dates}


def _client() -> TestClient:
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


# ---------------------------------------------------------------------------
# /api/portfolio/xray
# ---------------------------------------------------------------------------


class TestXrayEndpoint:
    def test_xray_returns_full_report(self) -> None:
        """A 200 with every risk section present and JSON-safe."""
        response = _client().post(
            "/api/portfolio/xray",
            json={"closes": _price_panel(), "weights": XRAY_WEIGHTS},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["inputs"]["symbols"] == ["AAA", "BBB", "CCC"]
        assert body["inputs"]["return_observations"] == 59
        assert body["concentration"]["hhi"] is not None
        assert body["concentration"]["effective_n"] is not None
        assert body["volatility"]["annualized_vol"] is not None
        assert body["volatility"]["downside_deviation_annualized"] is not None
        assert body["drawdown"]["max_drawdown"] is not None
        assert body["tail_risk"]["var_95"] is not None
        assert body["tail_risk"]["expected_shortfall_99"] is not None
        assert body["diversification"]["diversification_ratio"] is not None
        assert body["correlation"]["avg_pairwise_abs"] is not None
        assert body["correlation"]["max_pair"] is not None
        assert body["skipped"] == []
        assert body["warnings"] == []

    def test_xray_unknown_symbol_is_400(self) -> None:
        """Weights referencing symbols with no prices are a client error."""
        response = _client().post(
            "/api/portfolio/xray",
            json={"closes": _price_panel(), "weights": {"ZZZ": 1.0}},
        )
        assert response.status_code == 400
        assert "no price data" in response.json()["detail"]

    def test_xray_empty_closes_is_400(self) -> None:
        response = _client().post(
            "/api/portfolio/xray",
            json={"closes": {}, "weights": XRAY_WEIGHTS},
        )
        assert response.status_code == 400
        assert "empty" in response.json()["detail"]

    def test_xray_negative_weight_is_400(self) -> None:
        """The x-ray is long-only; negative weights must be rejected."""
        response = _client().post(
            "/api/portfolio/xray",
            json={
                "closes": _price_panel(),
                "weights": {"AAA": 1.2, "BBB": -0.2},
            },
        )
        assert response.status_code == 400
        assert "long-only" in response.json()["detail"]

    def test_xray_dates_length_mismatch_is_400(self) -> None:
        response = _client().post(
            "/api/portfolio/xray",
            json={
                "closes": _price_panel(),
                "weights": XRAY_WEIGHTS,
                "dates": ["2025-01-01", "2025-01-02"],
            },
        )
        assert response.status_code == 400
        assert "dates length" in response.json()["detail"]

    def test_xray_bad_var_levels_is_400(self) -> None:
        response = _client().post(
            "/api/portfolio/xray",
            json={
                "closes": _price_panel(),
                "weights": XRAY_WEIGHTS,
                "var_levels": [1.5],
            },
        )
        assert response.status_code == 400
        assert "var_levels" in response.json()["detail"]


# ---------------------------------------------------------------------------
# /api/portfolio/rebalance-notes
# ---------------------------------------------------------------------------


class TestRebalanceNotesEndpoint:
    def test_rebalance_notes_summarize_changes(self) -> None:
        """A single moved row becomes one rebalance with entries/exits."""
        target_pos = {
            "2025-01-01": {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2},
            "2025-02-01": {"AAA": 0.0, "BBB": 0.6, "CCC": 0.4},
            "2025-03-01": {"AAA": 0.0, "BBB": 0.6, "CCC": 0.4},
        }
        response = _client().post(
            "/api/portfolio/rebalance-notes", json={"target_pos": target_pos}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["rebalance_count"] == 1
        assert body["summary"]["largest_rebalance_date"] == "2025-02-01"
        assert body["summary"]["turnover_total"] > 0
        rebalance = body["rebalances"][0]
        assert rebalance["date"] == "2025-02-01"
        assert rebalance["exits"] == [{"code": "AAA", "weight": 0.5}]
        assert rebalance["top_moves"][0]["code"] == "AAA"

    def test_rebalance_notes_flat_frame_has_no_rebalances(self) -> None:
        flat = {
            "2025-01-01": {"AAA": 0.5, "BBB": 0.5},
            "2025-02-01": {"AAA": 0.5, "BBB": 0.5},
        }
        response = _client().post(
            "/api/portfolio/rebalance-notes", json={"target_pos": flat}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["rebalance_count"] == 0
        assert body["rebalances"] == []

    def test_rebalance_notes_bad_dates_is_400(self) -> None:
        response = _client().post(
            "/api/portfolio/rebalance-notes",
            json={"target_pos": {"not-a-date": {"AAA": 1.0}, "2025-01-02": {"AAA": 0.5}}},
        )
        assert response.status_code == 400
        assert "dates" in response.json()["detail"]


# ---------------------------------------------------------------------------
# /api/portfolio/constraints
# ---------------------------------------------------------------------------


class TestConstraintsEndpoint:
    def test_max_weight_clips_over_cap(self) -> None:
        frame = {
            "2025-01-01": {"AAA": 0.4, "BBB": 0.3, "CCC": 0.3},
            "2025-01-02": {"AAA": 0.2, "BBB": 0.4, "CCC": 0.4},
        }
        response = _client().post(
            "/api/portfolio/constraints",
            json={
                "frame": frame,
                "constraints": [{"type": "max_weight", "cap": 0.25}],
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["constraints"] == ["max_weight cap 0.25"]
        assert body["summary"]["adjusted_cells"] > 0
        for row in body["frame"].values():
            for weight in row.values():
                assert weight <= 0.25 + 1e-9

    def test_group_exposure_scales_down_group(self) -> None:
        frame = {
            "2025-01-01": {"AAA": 0.3, "BBB": 0.3, "CCC": 0.4},
            "2025-01-02": {"AAA": 0.25, "BBB": 0.35, "CCC": 0.4},
        }
        response = _client().post(
            "/api/portfolio/constraints",
            json={
                "frame": frame,
                "constraints": [
                    {
                        "type": "group_exposure",
                        "groups": {"AAA": "tech", "BBB": "tech", "CCC": "energy"},
                        "caps": {"tech": 0.4, "energy": 0.4},
                    }
                ],
            },
        )

        assert response.status_code == 200
        adjusted = response.json()["frame"]["2025-01-01"]
        assert adjusted["AAA"] + adjusted["BBB"] <= 0.4 + 1e-9
        assert adjusted["CCC"] == 0.4

    def test_empty_constraints_returns_frame_unchanged(self) -> None:
        frame = {
            "2025-01-01": {"AAA": 0.6, "BBB": 0.4},
            "2025-01-02": {"AAA": 0.5, "BBB": 0.5},
        }
        response = _client().post(
            "/api/portfolio/constraints",
            json={"frame": frame, "constraints": []},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["adjusted_cells"] == 0
        assert body["frame"] == frame

    def test_unknown_constraint_type_is_400(self) -> None:
        frame = {
            "2025-01-01": {"AAA": 1.0},
            "2025-01-02": {"AAA": 0.5},
        }
        response = _client().post(
            "/api/portfolio/constraints",
            json={
                "frame": frame,
                "constraints": [{"type": "magic_weights", "cap": 0.5}],
            },
        )
        assert response.status_code == 400
        assert "unknown constraint type" in response.json()["detail"]


# ---------------------------------------------------------------------------
# /api/portfolio/optimize
# ---------------------------------------------------------------------------


class TestOptimizeEndpoint:
    def test_optimize_returns_adjusted_frame(self) -> None:
        """The turnover-aware optimizer rewrites the signal frame row by row."""
        response = _client().post(
            "/api/portfolio/optimize",
            json={
                "returns": _returns_panel(),
                "positions": _positions_panel(),
                "lookback": 60,
                "risk_aversion": 1.0,
                "turnover_penalty": 0.0,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["optimizer"] == "turnover_aware"
        assert body["summary"]["lookback"] == 60
        assert body["summary"]["assets"] == ["AAA", "BBB", "CCC"]
        assert len(body["frame"]) == 70
        # Rows beyond the lookback window get rewritten by the optimizer.
        last = body["frame"][str(pd.bdate_range("2024-09-02", periods=70)[-1].date())]
        assert {"AAA", "BBB", "CCC"} <= set(last)

    def test_optimize_bad_cap_is_400(self) -> None:
        response = _client().post(
            "/api/portfolio/optimize",
            json={
                "returns": _returns_panel(),
                "positions": _positions_panel(),
                "max_per_name": 1.5,
            },
        )
        assert response.status_code == 400
        assert "max_per_name" in response.json()["detail"]

    def test_optimize_empty_positions_is_400(self) -> None:
        response = _client().post(
            "/api/portfolio/optimize",
            json={"returns": _returns_panel(), "positions": {}},
        )
        assert response.status_code == 400
        assert "positions" in response.json()["detail"]

    def test_optimize_unknown_group_cap_is_400(self) -> None:
        response = _client().post(
            "/api/portfolio/optimize",
            json={
                "returns": _returns_panel(),
                "positions": _positions_panel(),
                "max_per_group": {"not_a_group": 0.5},
            },
        )
        assert response.status_code == 400
        assert "no mapped assets" in response.json()["detail"]
