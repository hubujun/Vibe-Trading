"""Tests for the portfolio routes: upstream read-only dashboard
and local Portfolio Studio computation endpoints.
"""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import portfolio_routes


class _Service:
    def __init__(self, **kwargs):
        self.progress_callback = kwargs.get("progress_callback")

    def latest(self):
        return {"snapshot_id": "latest"}

    def refresh(self):
        return {"snapshot_id": "new"}

    def settings(self):
        return {
            "display_currency": "USD",
            "sources": [{"connection_id": "ibkr", "enabled": True}],
        }

    def save_settings(self, payload):
        return payload

    def sources(self):
        return [{"profile_id": "ibkr-live-official-mcp-readonly"}]

    def history(self, limit):
        return [{"id": "one", "limit": limit}]

    def reconnect_target(self, source_id):
        return object(), object(), object()

    def export_csv(self):
        return "broker,symbol\nibkr,AAPL\n"

    def analysis_context(self):
        return {"privacy": "sanitized"}


def test_portfolio_routes_are_readonly_and_return_expected_shapes(monkeypatch):
    monkeypatch.setattr(portfolio_routes, "PortfolioService", _Service)

    class _SuccessfulProcess:
        def wait(self, timeout):
            return 0

    monkeypatch.setattr(
        portfolio_routes.subprocess,
        "Popen",
        lambda *args, **kwargs: _SuccessfulProcess(),
    )
    app = FastAPI()
    portfolio_routes.register_portfolio_routes(app)
    client = TestClient(app)

    assert client.get("/api/portfolio").json()["snapshot"]["snapshot_id"] == "latest"
    assert (
        client.post("/api/portfolio/refresh").json()["snapshot"]["snapshot_id"] == "new"
    )
    refresh = client.get("/api/portfolio/refresh-status").json()["refresh"]
    assert refresh["running"] is False
    assert refresh["sources"] == refresh["brokers"]
    assert (
        client.get("/api/portfolio/history?limit=12").json()["history"][0]["limit"]
        == 12
    )
    settings = client.get("/api/portfolio/settings").json()
    assert settings["settings"]["display_currency"] == "USD"
    saved = client.put(
        "/api/portfolio/settings",
        json={
            "display_currency": "CNY",
            "sources": [
                {
                    "connection_id": "ibkr",
                    "label": "Main",
                    "enabled": True,
                    "order": 0,
                    "include_cash": True,
                }
            ],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["settings"]["display_currency"] == "CNY"
    reconnect = client.post("/api/portfolio/sources/ibkr/reconnect")
    assert reconnect.status_code == 202
    for _ in range(50):
        reconnect_status = client.get("/api/portfolio/reconnect-status").json()[
            "reconnect"
        ]
        if not reconnect_status["running"]:
            break
        time.sleep(0.01)
    assert reconnect_status["source_id"] == "ibkr"
    assert reconnect_status["status"] == "authorized"
    assert (
        client.get("/api/portfolio/analysis-context").json()["context"]["privacy"]
        == "sanitized"
    )
    exported = client.get("/api/portfolio/export.csv")
    assert exported.status_code == 200
    assert "ibkr,AAPL" in exported.text


from types import SimpleNamespace

def test_concurrent_portfolio_reconnect_returns_conflict(monkeypatch):
    monkeypatch.setattr(portfolio_routes, "PortfolioService", _Service)
    app = FastAPI()
    portfolio_routes.register_portfolio_routes(app)
    client = TestClient(app)

    assert portfolio_routes._RECONNECT_OPERATION_LOCK.acquire(blocking=False)
    try:
        response = client.post("/api/portfolio/sources/ibkr/reconnect")
    finally:
        portfolio_routes._RECONNECT_OPERATION_LOCK.release()

    assert response.status_code == 409
    assert response.json()["detail"] == "portfolio reconnect already running"


def test_reconnect_timeout_stops_worker_and_releases_operation_lock(monkeypatch):
    class _TimedOutProcess:
        def __init__(self):
            self.terminated = False

        def wait(self, timeout):
            if not self.terminated:
                raise portfolio_routes.subprocess.TimeoutExpired("oauth", timeout)
            return -15

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def poll(self):
            return None

    process = _TimedOutProcess()
    monkeypatch.setattr(
        portfolio_routes.subprocess, "Popen", lambda *args, **kwargs: process
    )
    assert portfolio_routes._RECONNECT_OPERATION_LOCK.acquire(blocking=False)

    portfolio_routes._run_reconnect("ibkr")

    state = portfolio_routes._reconnect_snapshot()
    assert state["running"] is False
    assert state["status"] == "timeout"
    assert process.terminated is True
    assert portfolio_routes._RECONNECT_OPERATION_LOCK.acquire(blocking=False)
    portfolio_routes._RECONNECT_OPERATION_LOCK.release()


import numpy as np
import pandas as pd
from fastapi import FastAPI
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


# ---------------------------------------------------------------------------
# /api/portfolio/rebalance-plan + rebalance-execute
# ---------------------------------------------------------------------------


class _FakeRebalanceEngine:
    """In-memory engine double honoring the config gates (no I/O)."""

    def __init__(
        self,
        *,
        max_order_notional_usd: float = 25.0,
        max_total_exposure_usd: float = 200.0,
        max_trades_per_day: int = 10,
        open_exposure: float = 0.0,
        orders_today: int = 0,
    ) -> None:
        self.config = SimpleNamespace(
            max_order_notional_usd=max_order_notional_usd,
            max_total_exposure_usd=max_total_exposure_usd,
            max_trades_per_day=max_trades_per_day,
        )
        self._open_exposure = open_exposure
        self._orders_today = orders_today
        self.placed: list[tuple[str, str, float]] = []

    def open_exposure_usd(self) -> float:
        return self._open_exposure

    def orders_today(self) -> int:
        return self._orders_today

    def place_order(self, symbol: str, side: str, notional: float) -> dict:
        self.placed.append((symbol, side, notional))
        return {
            "status": "ok",
            "symbol": symbol,
            "side": side,
            "notional": notional,
        }


def _rebalance_client(
    engine: _FakeRebalanceEngine | None = None,
    live_factory=None,
) -> TestClient:
    from src.api.portfolio_routes import register_portfolio_routes

    engine = engine or _FakeRebalanceEngine()
    app = FastAPI()
    register_portfolio_routes(
        app,
        require_auth=lambda: None,
        paper_engine_factory=lambda: engine,
        live_executor_factory=live_factory,
    )
    return TestClient(app, client=("127.0.0.1", 50000))


class TestRebalancePlanEndpoint:
    def test_plan_diffs_into_orders(self) -> None:
        """Held symbols rebalance and new targets open with buys."""
        engine = _FakeRebalanceEngine(
            max_order_notional_usd=1000.0, max_total_exposure_usd=5000.0,
        )
        response = _rebalance_client(engine).post(
            "/api/portfolio/rebalance-plan",
            json={
                "target_weights": {"AAA": 0.5, "BBB": 0.5},
                "current_positions": {"AAA": 400.0},
                "portfolio_value": 1000.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["execution"] == "preview"
        assert body["orders"] == [
            {
                "symbol": "AAA",
                "side": "buy",
                "notional": 100.0,
                "current_notional": 400.0,
                "target_notional": 500.0,
            },
            {
                "symbol": "BBB",
                "side": "buy",
                "notional": 500.0,
                "current_notional": 0.0,
                "target_notional": 500.0,
            },
        ]
        assert body["summary"]["order_count"] == 2
        assert body["summary"]["buy_notional"] == 600.0

    def test_plan_closes_untargeted_positions(self) -> None:
        """Held symbols absent from the target are fully closed."""
        engine = _FakeRebalanceEngine(
            max_order_notional_usd=1000.0, max_total_exposure_usd=5000.0,
        )
        response = _rebalance_client(engine).post(
            "/api/portfolio/rebalance-plan",
            json={
                "target_weights": {"AAA": 1.0},
                "current_positions": {"AAA": 500.0, "BBB": 300.0},
                "portfolio_value": 1000.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        sides = {o["symbol"]: o["side"] for o in body["orders"]}
        assert sides == {"AAA": "buy", "BBB": "sell"}
        bbb = next(o for o in body["orders"] if o["symbol"] == "BBB")
        assert bbb["notional"] == 300.0
        assert bbb["target_notional"] == 0.0

    def test_plan_skips_sub_min_notional_orders(self) -> None:
        response = _rebalance_client().post(
            "/api/portfolio/rebalance-plan",
            json={
                "target_weights": {"AAA": 0.5, "BBB": 0.5},
                "current_positions": {"AAA": 1000.0},
                "portfolio_value": 1000.0,
                "min_notional": 600.0,
            },
        )
        assert response.status_code == 200
        assert response.json()["orders"] == []

    def test_plan_empty_weights_is_400(self) -> None:
        response = _rebalance_client().post(
            "/api/portfolio/rebalance-plan",
            json={"target_weights": {}, "portfolio_value": 1000.0},
        )
        assert response.status_code == 400
        assert "target_weights" in response.json()["detail"]

    def test_plan_negative_weight_is_400(self) -> None:
        response = _rebalance_client().post(
            "/api/portfolio/rebalance-plan",
            json={"target_weights": {"AAA": 1.2, "BBB": -0.2}, "portfolio_value": 1000.0},
        )
        assert response.status_code == 400
        assert "long-only" in response.json()["detail"]

    def test_plan_weights_not_summing_to_one_is_400(self) -> None:
        response = _rebalance_client().post(
            "/api/portfolio/rebalance-plan",
            json={"target_weights": {"AAA": 0.7}, "portfolio_value": 1000.0},
        )
        assert response.status_code == 400
        assert "sum to 1.0" in response.json()["detail"]

    def test_plan_blocks_over_cap_orders(self) -> None:
        """Orders above max_order_notional_usd land in blocked, not orders."""
        response = _rebalance_client().post(
            "/api/portfolio/rebalance-plan",
            json={"target_weights": {"AAA": 1.0}, "portfolio_value": 1000.0},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["orders"] == []
        assert body["blocked"][0]["symbol"] == "AAA"
        assert "max_order_notional_usd" in body["blocked"][0]["reason"]
        assert body["summary"]["blocked_count"] == 1

    def test_plan_blocks_on_exposure_limit(self) -> None:
        """Buy orders beyond the aggregate exposure cap are blocked."""
        engine = _FakeRebalanceEngine(
            open_exposure=180.0, max_total_exposure_usd=200.0,
            max_order_notional_usd=1000.0,
        )
        response = _rebalance_client(engine).post(
            "/api/portfolio/rebalance-plan",
            json={"target_weights": {"AAA": 1.0}, "portfolio_value": 1000.0},
        )
        assert response.status_code == 200
        blocked = response.json()["blocked"]
        assert len(blocked) == 1
        assert blocked[0]["reason"] == "exposure limit reached"

    def test_plan_blocks_on_daily_quota(self) -> None:
        """Buys after max_trades_per_day are blocked; sells still pass."""
        engine = _FakeRebalanceEngine(
            orders_today=10, max_trades_per_day=10,
            max_order_notional_usd=1000.0,
            max_total_exposure_usd=5000.0,
        )
        response = _rebalance_client(engine).post(
            "/api/portfolio/rebalance-plan",
            json={
                "target_weights": {"AAA": 1.0},
                "current_positions": {"AAA": 300.0, "BBB": 500.0},
                "portfolio_value": 1000.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert [o["symbol"] for o in body["orders"]] == ["BBB"]
        assert body["orders"][0]["side"] == "sell"
        assert body["blocked"][0]["reason"] == "daily order limit reached"


class TestRebalanceExecuteEndpoint:
    def test_execute_paper_submits_orders(self) -> None:
        """Paper execution drives the engine with the planned notionals."""
        engine = _FakeRebalanceEngine()
        response = _rebalance_client(engine).post(
            "/api/portfolio/rebalance-execute",
            json={
                "target_weights": {"AAA": 0.5, "BBB": 0.5},
                "portfolio_value": 40.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["execution"] == "paper"
        assert body["summary"] == {
            "submitted": 2,
            "ok": 2,
            "rejected": 0,
            "failed": 0,
            "blocked_count": 0,
        }
        assert engine.placed == [("AAA", "buy", 20.0), ("BBB", "buy", 20.0)]
        assert all(o["status"] == "ok" for o in body["orders"])

    def test_execute_blocked_orders_never_touch_engine(self) -> None:
        """Risk-blocked orders are skipped before any engine call."""
        engine = _FakeRebalanceEngine(max_order_notional_usd=25.0)
        response = _rebalance_client(engine).post(
            "/api/portfolio/rebalance-execute",
            json={"target_weights": {"AAA": 1.0}, "portfolio_value": 1000.0},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"] == {
            "submitted": 0,
            "ok": 0,
            "rejected": 0,
            "failed": 0,
            "blocked_count": 1,
        }
        assert engine.placed == []

    def test_execute_unknown_execution_is_400(self) -> None:
        response = _rebalance_client().post(
            "/api/portfolio/rebalance-execute",
            json={
                "target_weights": {"AAA": 1.0},
                "portfolio_value": 1000.0,
                "execution": "hedge",
            },
        )
        assert response.status_code == 400
        assert "execution" in response.json()["detail"]

    def test_execute_live_without_confirm_is_400(self) -> None:
        """The confirm red line is enforced before any live call."""
        live = _FakeRebalanceEngine()
        response = _rebalance_client(live_factory=lambda: live).post(
            "/api/portfolio/rebalance-execute",
            json={
                "target_weights": {"AAA": 1.0},
                "portfolio_value": 40.0,
                "execution": "live",
            },
        )
        assert response.status_code == 400
        assert "confirm=true" in response.json()["detail"]
        assert live.placed == []

    def test_execute_live_without_configured_executor_is_503(self) -> None:
        response = _rebalance_client().post(
            "/api/portfolio/rebalance-execute",
            json={
                "target_weights": {"AAA": 1.0},
                "portfolio_value": 40.0,
                "execution": "live",
                "confirm": True,
            },
        )
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]

    def test_execute_live_with_confirm_calls_live_executor(self) -> None:
        """Confirm + configured executor routes through the live executor."""
        live = _FakeRebalanceEngine(max_order_notional_usd=1000.0)
        response = _rebalance_client(
            _FakeRebalanceEngine(
                max_order_notional_usd=1000.0, max_total_exposure_usd=5000.0,
            ),
            live_factory=lambda: live,
        ).post(
            "/api/portfolio/rebalance-execute",
            json={
                "target_weights": {"AAA": 1.0},
                "portfolio_value": 40.0,
                "execution": "live",
                "confirm": True,
                "note": "manual rebalance",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["execution"] == "live"
        assert body["note"] == "manual rebalance"
        assert body["summary"]["ok"] == 1
        assert live.placed == [("AAA", "buy", 40.0)]

    def test_execute_reports_engine_rejections_per_order(self) -> None:
        """Engine rejections surface per order without failing the batch."""
        engine = _FakeRebalanceEngine(max_order_notional_usd=1000.0)

        def reject(symbol: str, side: str, notional: float) -> dict:
            return {"status": "rejected", "error": "halt is active"}

        engine.place_order = reject  # type: ignore[method-assign]
        response = _rebalance_client(engine).post(
            "/api/portfolio/rebalance-execute",
            json={"target_weights": {"AAA": 1.0}, "portfolio_value": 40.0},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["rejected"] == 1
        assert body["orders"][0]["status"] == "rejected"
        assert body["orders"][0]["detail"] == "halt is active"
