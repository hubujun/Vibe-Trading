"""API tests for the crypto autopilot status endpoint.

Covers ``GET /api/autopilot/status``: the aggregate of pipeline state,
heartbeat liveness, the kill-switch sentinel, the daily order counter, and
the config summary. All persisted artifacts are stubbed under a tmp root —
no real autopilot process or broker is touched.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

import api_server
from src.api import autopilot_routes

__all__ = []


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    # Point the module-level runtime root at a temp dir so the state/heartbeat
    # files and the daily counter all resolve under tmp_path.
    monkeypatch.setattr(autopilot_routes, "_RUNTIME_ROOT", tmp_path)
    # Redirect the live tree (``~/.vibe-trading``) so the HALT sentinel also
    # resolves under tmp_path (mirrors test_api_live_runtime).
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path), raising=False)
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _write_state(tmp_path: Path, payload: dict) -> None:
    """Write a pipeline-state payload under the autopilot subdirectory."""
    autopilot_dir = tmp_path / "autopilot"
    autopilot_dir.mkdir(parents=True, exist_ok=True)
    (autopilot_dir / "state.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_heartbeat(tmp_path: Path, timestamp_ms: int) -> None:
    """Write a heartbeat payload under the autopilot subdirectory."""
    autopilot_dir = tmp_path / "autopilot"
    autopilot_dir.mkdir(parents=True, exist_ok=True)
    (autopilot_dir / "heartbeat.json").write_text(
        json.dumps({"runner_id": "test", "timestamp_ms": timestamp_ms, "pid": 1}),
        encoding="utf-8",
    )


def _write_counter(tmp_path: Path, date: str, count: int) -> None:
    """Write a daily-order-counter payload at the runtime root."""
    (tmp_path / "daily_orders.json").write_text(
        json.dumps({"date": date, "count": count}), encoding="utf-8"
    )


def _write_halt(tmp_path: Path, payload: dict) -> None:
    """Write a per-broker HALT sentinel under the (redirected) live tree.

    The live tree resolves to ``<runtime_root>/live`` where the runtime root
    is ``Path.home() / ".vibe-trading"`` — so with ``Path.home`` redirected
    to *tmp_path* the sentinel lands under ``tmp_path/.vibe-trading/live``.
    """
    halt_dir = tmp_path / ".vibe-trading" / "live" / "okx"
    halt_dir.mkdir(parents=True, exist_ok=True)
    (halt_dir / "HALT").write_text(json.dumps(payload), encoding="utf-8")


def _write_trades(tmp_path: Path, records: list[dict]) -> None:
    """Append trade-ledger records under the runtime root."""
    from src.crypto_autopilot.trade_ledger import write_trade_record

    for record in records:
        write_trade_record(
            tmp_path,
            engine=record["engine"],
            symbol=record["symbol"],
            side=record["side"],
            notional=record["notional"],
            quantity=record.get("quantity"),
            price=record.get("price"),
            realized_pnl=record.get("realized_pnl"),
            alpha_id=record.get("alpha_id"),
            ts=record.get("ts"),
        )


# ---------------------------------------------------------------------------
# Dormant / empty state
# ---------------------------------------------------------------------------


class TestDormantState:
    def test_status_dormant_by_default(self, tmp_path: Path, monkeypatch) -> None:
        """No artifacts → idle, not alive, not halted, zero counter."""
        client = _client(tmp_path, monkeypatch)

        response = client.get("/api/autopilot/status")

        assert response.status_code == 200
        body = response.json()
        assert body["pipeline"]["phase"] == "idle"
        assert body["pipeline"]["tick_count"] == 0
        assert body["health"]["alive"] is False
        assert body["health"]["stale"] is True
        assert body["halt"]["halted"] is False
        assert body["counter"]["count"] == 0
        assert body["config"]["enabled"] is False

    def test_status_includes_config_summary(self, tmp_path: Path, monkeypatch) -> None:
        """The config summary exposes the operator-facing knobs."""
        monkeypatch.delenv("AUTOPILOT_PAIRS", raising=False)
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/status").json()
        config = body["config"]
        assert config["pairs"][:2] == ["BTC-USDT", "ETH-USDT"]
        assert len(config["pairs"]) == 10
        assert config["max_trades_per_day"] == 10
        assert config["trade_interval_minutes"] == 5


# ---------------------------------------------------------------------------
# Pipeline state + liveness
# ---------------------------------------------------------------------------


class TestPipelineState:
    def test_status_reflects_persisted_state(self, tmp_path: Path, monkeypatch) -> None:
        """A persisted state file drives the pipeline section."""
        _write_state(
            tmp_path,
            {
                "phase": "paper_trading",
                "active_factor_id": "crypto_momentum_1h",
                "last_tick_at": "2026-08-09T00:00:00+00:00",
                "tick_count": 42,
                "updated_at": "2026-08-09T00:05:00+00:00",
            },
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/status").json()
        pipeline = body["pipeline"]
        assert pipeline["phase"] == "paper_trading"
        assert pipeline["active_factor_id"] == "crypto_momentum_1h"
        assert pipeline["tick_count"] == 42
        assert pipeline["last_tick_at"] == "2026-08-09T00:00:00+00:00"

    def test_status_alive_with_fresh_heartbeat_and_state(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A fresh heartbeat + state file mark the loop alive."""
        _write_state(tmp_path, {"phase": "collecting", "tick_count": 1})
        _write_heartbeat(tmp_path, int(time.time() * 1000))
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/status").json()
        assert body["health"]["alive"] is True
        assert body["health"]["stale"] is False

    def test_status_stale_without_heartbeat(self, tmp_path: Path, monkeypatch) -> None:
        """A state file without a heartbeat is not alive (fail-closed)."""
        _write_state(tmp_path, {"phase": "live", "tick_count": 7})
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/status").json()
        assert body["health"]["alive"] is False
        assert body["health"]["stale"] is True


# ---------------------------------------------------------------------------
# Kill switch + daily counter
# ---------------------------------------------------------------------------


class TestHaltAndCounter:
    def test_status_reflects_halt_sentinel(self, tmp_path: Path, monkeypatch) -> None:
        """A tripped HALT sentinel surfaces its attribution metadata."""
        _write_halt(
            tmp_path,
            {
                "by": "cli",
                "reason": "manual stop via autopilot CLI",
                "tripped_at": "2026-08-09T01:00:00+00:00",
            },
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/status").json()
        halt = body["halt"]
        assert halt["halted"] is True
        assert halt["reason"] == "manual stop via autopilot CLI"
        assert halt["tripped_by"] == "cli"
        assert halt["tripped_at"] == "2026-08-09T01:00:00+00:00"

    def test_status_reflects_daily_counter(self, tmp_path: Path, monkeypatch) -> None:
        """The persisted counter surfaces date + count for the cap gauge."""
        _write_counter(tmp_path, "2026-08-09", 6)
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/status").json()
        assert body["counter"]["date"] == "2026-08-09"
        assert body["counter"]["count"] == 6

    def test_status_handles_corrupt_counter(self, tmp_path: Path, monkeypatch) -> None:
        """A corrupt counter file degrades to zero instead of 500ing."""
        (tmp_path / "daily_orders.json").write_text("{nope", encoding="utf-8")
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/status").json()
        assert body["counter"]["count"] == 0

    def test_status_surfaces_data_health(self, tmp_path: Path, monkeypatch) -> None:
        """The persisted freshness snapshot reaches the status payload."""
        (tmp_path / "data_health.json").write_text(
            json.dumps({
                "updated_at": "2026-08-09T02:00:00+00:00",
                "stale_symbols": ["LTC-USDT"],
                "symbols": {
                    "BTC-USDT": {"latest_ts": "2026-08-09T01:00:00", "lag_hours": 0.5},
                    "LTC-USDT": {"latest_ts": "2026-08-08T01:00:00", "lag_hours": 25.0},
                },
            }),
            encoding="utf-8",
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/status").json()
        health = body["data_health"]
        assert health["stale_symbols"] == ["LTC-USDT"]
        assert health["symbols"]["BTC-USDT"]["lag_hours"] == 0.5
        assert health["updated_at"] == "2026-08-09T02:00:00+00:00"

    def test_status_data_health_fails_open(self, tmp_path: Path, monkeypatch) -> None:
        """A missing/corrupt freshness file degrades to an empty snapshot."""
        (tmp_path / "data_health.json").write_text("{nope", encoding="utf-8")
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/status").json()
        assert body["data_health"]["stale_symbols"] == []
        assert body["data_health"]["symbols"] == {}


# ---------------------------------------------------------------------------
# Factors endpoint (lifecycle snapshot)
# ---------------------------------------------------------------------------


def _write_factors_snapshot(tmp_path: Path, payload: dict) -> None:
    """Write a factors.json snapshot at the module-level runtime root."""
    (tmp_path / "factors.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


class TestFactorsEndpoint:
    def test_factors_empty_by_default(self, tmp_path: Path, monkeypatch) -> None:
        """No snapshot → empty active/pending/retired with zoo inventory."""
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/factors").json()

        assert body["active"] == []
        assert body["pending"] == []
        assert body["retired"] == []
        assert "updated_at" in body

    def test_factors_surfaces_bench_metrics_and_retired(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Active factors carry bench metrics; retired factors carry audit."""
        _write_factors_snapshot(
            tmp_path,
            {
                "active": [
                    {
                        "alpha_id": "momentum_1h",
                        "lifecycle": "backtested",
                        "screen_ic_mean": 0.021,
                        "ic_mean": 0.034,
                        "alpha_t_full": 2.4,
                        "category": "confirmed_alive",
                    }
                ],
                "pending": ["candidate_a"],
                "retired": [
                    {
                        "alpha_id": "old_signal",
                        "retired_at": "2026-08-09T01:00:00+00:00",
                        "reason": "decay scan: ic_drift signal",
                    }
                ],
            },
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/factors").json()

        assert body["active"][0]["alpha_id"] == "momentum_1h"
        assert body["active"][0]["ic_mean"] == 0.034
        assert body["active"][0]["alpha_t_full"] == 2.4
        assert body["pending"] == ["candidate_a"]
        assert body["retired"][0]["reason"].startswith("decay scan")
        assert body["retired"][0]["retired_at"].startswith("2026-08-09")


# ---------------------------------------------------------------------------
# Positions endpoint (open paper positions)
# ---------------------------------------------------------------------------


class TestPositionsEndpoint:
    def test_positions_empty_by_default(self, tmp_path: Path, monkeypatch) -> None:
        """No open positions → an empty list with count 0."""
        monkeypatch.setattr(
            "src.crypto_autopilot.paper_engine.PaperEngine.get_positions",
            lambda self: [],
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/positions").json()

        assert body == {"positions": [], "count": 0}

    def test_positions_serialize_mark_to_market(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Open positions surface qty/entry/mark and unrealized P&L."""
        from datetime import datetime, timezone

        from src.crypto_autopilot.types import PaperPosition

        def _fake_positions(self):
            return [
                PaperPosition(
                    symbol="BTC-USDT",
                    side="long",
                    quantity=0.000462,
                    entry_price=64961.2,
                    entry_time=datetime(2026, 8, 9, 19, 34, 38, tzinfo=timezone.utc),
                    unrealized_pnl=1.25,
                )
            ]

        monkeypatch.setattr(
            "src.crypto_autopilot.paper_engine.PaperEngine.get_positions",
            _fake_positions,
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/positions").json()

        assert body["count"] == 1
        pos = body["positions"][0]
        assert pos["symbol"] == "BTC-USDT"
        assert pos["quantity"] == 0.000462
        assert pos["entry_price"] == 64961.2
        assert pos["unrealized_pnl"] == 1.25
        assert pos["entry_time"].startswith("2026-08-09T19:34:38")


class TestTradesEndpoint:
    def test_trades_empty_by_default(self, tmp_path: Path, monkeypatch) -> None:
        """No ledger → an empty trades list with count 0."""
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/trades").json()

        assert body == {"trades": [], "count": 0}

    def test_trades_newest_first(self, tmp_path: Path, monkeypatch) -> None:
        """Ledger records surface newest first with full fields."""
        _write_trades(
            tmp_path,
            [
                {
                    "engine": "paper", "symbol": "BTC-USDT", "side": "buy",
                    "notional": 50.0, "quantity": 0.5, "price": 100.0,
                    "ts": "2026-08-09T00:00:00+00:00",
                },
                {
                    "engine": "live", "symbol": "ETH-USDT", "side": "sell",
                    "notional": 20.0, "realized_pnl": 1.5,
                    "ts": "2026-08-09T01:00:00+00:00",
                },
            ],
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/trades").json()

        assert body["count"] == 2
        assert [t["symbol"] for t in body["trades"]] == ["ETH-USDT", "BTC-USDT"]
        latest = body["trades"][0]
        assert latest["engine"] == "live"
        assert latest["realized_pnl"] == 1.5
        assert latest["quantity"] is None  # live fills carry no price/qty

    def test_trades_filters(self, tmp_path: Path, monkeypatch) -> None:
        """Engine and symbol query filters narrow the returned records."""
        _write_trades(
            tmp_path,
            [
                {
                    "engine": "paper", "symbol": "BTC-USDT", "side": "buy",
                    "notional": 50.0,
                },
                {
                    "engine": "live", "symbol": "BTC-USDT", "side": "buy",
                    "notional": 60.0,
                },
                {
                    "engine": "live", "symbol": "ETH-USDT", "side": "sell",
                    "notional": 20.0,
                },
            ],
        )
        client = _client(tmp_path, monkeypatch)

        paper = client.get("/api/autopilot/trades", params={"engine": "paper"}).json()
        assert paper["count"] == 1
        assert paper["trades"][0]["notional"] == 50.0

        btc = client.get("/api/autopilot/trades", params={"symbol": "btc-usdt"}).json()
        assert btc["count"] == 2

        empty = client.get(
            "/api/autopilot/trades", params={"engine": "paper", "symbol": "ETH-USDT"}
        ).json()
        assert empty["count"] == 0


class TestPerformanceEndpoint:
    def test_performance_empty_has_no_benchmark(self, tmp_path: Path, monkeypatch) -> None:
        """No ledger days → benchmark fields stay None (nothing to compare)."""
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/performance").json()

        assert body["benchmark_symbol"] is None
        assert body["benchmark_return_pct"] is None
        assert body["avg_slippage_bps"] is None
        assert body["realized_pnl_usd"] == 0.0

    def test_performance_reports_avg_slippage_bps(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Slippage measurements surface as an average bps figure."""
        from src.crypto_autopilot.trade_ledger import append_slippage_record

        append_slippage_record(
            tmp_path, symbol="BTC-USDT", signal_price=100.0, fill_price=100.01,
        )
        append_slippage_record(
            tmp_path, symbol="ETH-USDT", signal_price=100.0, fill_price=99.99,
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/performance").json()

        assert body["avg_slippage_bps"] == 0.0  # (1.0 + -1.0) / 2

    def test_performance_surfaces_benchmark_return(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A ledger day + fake benchmark bars yield a vs-BTC return."""
        import pandas as pd

        from src.crypto_autopilot.market_feed import MarketFeed

        def _fake_bars(self, symbol, period="1d", limit=400):
            # Daily closes rising 100.0 → 109.75 over 2026-07-01..2026-08-09.
            idx = pd.date_range("2026-07-01", periods=40, freq="D")
            closes = [100.0 + i * 0.25 for i in range(40)]
            return pd.DataFrame(
                {
                    "open": closes, "high": [c + 0.5 for c in closes],
                    "low": [c - 0.5 for c in closes], "close": closes,
                    "volume": 1.0,
                },
                index=idx,
            )

        monkeypatch.setattr(MarketFeed, "fetch_bars", _fake_bars)
        _write_trades(
            tmp_path,
            [
                {
                    "engine": "paper", "symbol": "BTC-USDT", "side": "sell",
                    "notional": 50.0, "realized_pnl": 1.25,
                    "ts": "2026-08-01T00:00:00+00:00",
                }
            ],
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/performance").json()

        # Benchmark window starts 2026-08-01 (first ledger day): close at
        # index 31 = 107.75, latest close = 109.75 → +1.86%.
        assert body["benchmark_symbol"] == "BTC-USDT"
        assert body["benchmark_return_pct"] == 1.86
        assert body["realized_pnl_usd"] == 1.25

    def test_performance_benchmark_degrades_on_feed_failure(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A failing benchmark fetch yields None fields, not a 500."""
        from src.crypto_autopilot.market_feed import MarketFeed

        def _raise(self, symbol, period="1d", limit=400):
            raise RuntimeError("feed down")

        monkeypatch.setattr(MarketFeed, "fetch_bars", _raise)
        _write_trades(
            tmp_path,
            [
                {
                    "engine": "paper", "symbol": "BTC-USDT", "side": "sell",
                    "notional": 50.0, "realized_pnl": 0.5,
                    "ts": "2026-08-01T00:00:00+00:00",
                }
            ],
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/performance").json()

        assert body["benchmark_symbol"] is None
        assert body["benchmark_return_pct"] is None
        assert body["realized_pnl_usd"] == 0.5

    def test_gap_empty_by_default(self, tmp_path: Path, monkeypatch) -> None:
        """No ledger/slippage data → empty sections, no 500."""
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/gap").json()

        assert body["by_symbol"] == {}
        assert body["by_factor"] == {}
        assert body["slippage"]["records"] == 0
        assert body["live_scale"] == 5.0

    def test_gap_aggregates_paper_live_and_scale(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Ledger + slippage data surface in the gap report."""
        from datetime import datetime, timedelta, timezone

        from src.crypto_autopilot.live_scale import save_live_scale
        from src.crypto_autopilot.trade_ledger import append_slippage_record

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        _write_trades(
            tmp_path,
            [
                {
                    "engine": "paper", "symbol": "BTC-USDT", "side": "buy",
                    "notional": 25.0, "price": 100.0, "fee": 0.02,
                    "alpha_id": "alpha_gap_01", "ts": now,
                },
                {
                    "engine": "live", "symbol": "BTC-USDT", "side": "buy",
                    "notional": 5.0, "price": 100.04, "fee": 0.004,
                    "alpha_id": "alpha_gap_01", "ts": now,
                },
            ],
        )
        append_slippage_record(
            tmp_path, symbol="BTC-USDT",
            signal_price=100.0, fill_price=100.01, ts=now,
        )
        save_live_scale(tmp_path, scale=10.0, tier_index=1)
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/autopilot/gap").json()

        entry = body["by_symbol"]["BTC-USDT"]
        assert entry["paper"]["count"] == 1
        assert entry["live"]["count"] == 1
        assert entry["price_gap_bps"] == -4.0  # paper 100.00 vs live 100.04
        assert body["slippage"]["records"] == 1
        assert body["slippage"]["avg_bps"] == 1.0
        assert body["live_scale"] == 10.0
        assert body["live_scale_state"]["tier_index"] == 1
        assert body["by_factor"]["alpha_gap_01"]["price_gap_bps"] == -4.0
