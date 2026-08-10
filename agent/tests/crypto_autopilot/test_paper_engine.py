"""Tests for the OKX demo-account paper-trading engine.

Covers PnL calculation (rolling Sharpe, max drawdown, daily PnL),
position tracking (fill updates, get_positions, unrealized PnL),
and order enforcement (notional cap rejection, valid order success).

All OKX SDK calls are mocked — no real API requests are made.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from src.crypto_autopilot.config import AutopilotConfig
from src.crypto_autopilot.paper_engine import PaperEngine
from src.crypto_autopilot.types import PaperPosition

__all__ = []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> PaperEngine:
    """A PaperEngine with default config and a temp trade ledger root."""
    return PaperEngine(config=AutopilotConfig(max_order_notional_usd=50.0), runtime_root=tmp_path)


@pytest.fixture
def mock_place_order(monkeypatch):
    """Mock okx_sdk.place_order to return a success response."""
    def _fake_place_order(config, **kwargs):
        return {"status": "ok", "order_id": "test-123", "symbol": kwargs.get("symbol", "")}

    monkeypatch.setattr(
        "src.crypto_autopilot.paper_engine.okx_sdk.place_order",
        _fake_place_order,
    )
    return _fake_place_order


# ---------------------------------------------------------------------------
# 1. PnL calculation
# ---------------------------------------------------------------------------


class TestPnlCalculation:
    """Verify Sharpe, drawdown, and daily PnL computations."""

    def test_compute_rolling_sharpe_known_series(self, engine: PaperEngine) -> None:
        """Sharpe = mean/std * sqrt(bars_per_year) with known PnL series.

        pnl = [0, 1, 2] → mean=1, std(ddof=1)=1, Sharpe = 1 * sqrt(365).
        """
        engine._daily_pnl = [
            ("2024-01-01", 0.0),
            ("2024-01-02", 1.0),
            ("2024-01-03", 2.0),
        ]
        sharpe = engine.compute_rolling_sharpe(window_days=30)
        assert sharpe == pytest.approx(math.sqrt(365))

    def test_compute_rolling_sharpe_too_few_points(self, engine: PaperEngine) -> None:
        """Fewer than 2 data points → 0.0."""
        engine._daily_pnl = [("2024-01-01", 5.0)]
        assert engine.compute_rolling_sharpe() == 0.0

    def test_compute_max_drawdown_known_curve(self, engine: PaperEngine) -> None:
        """Drawdown fraction from a known equity curve.

        pnl = [100, -50] → cum = [100, 50], max_dd = 50, peak = 100 → 0.5.
        """
        engine._daily_pnl = [
            ("2024-01-01", 100.0),
            ("2024-01-02", -50.0),
        ]
        dd = engine.compute_max_drawdown()
        assert dd == pytest.approx(0.5)

    def test_compute_max_drawdown_too_few_entries(self, engine: PaperEngine) -> None:
        """Fewer than 2 entries → 0.0."""
        engine._daily_pnl = [("2024-01-01", 10.0)]
        assert engine.compute_max_drawdown() == 0.0

    def test_compute_daily_pnl_no_positions_returns_zero(self, engine: PaperEngine) -> None:
        """With no open positions, daily PnL is 0.0 (realized + unrealized)."""
        pnl = engine.compute_daily_pnl()
        assert pnl == 0.0


# ---------------------------------------------------------------------------
# 2. Position tracking
# ---------------------------------------------------------------------------


class TestPositionTracking:
    """Verify the local position book updates correctly after fills."""

    def test_position_book_updates_after_buy_fill(
        self, engine: PaperEngine, mock_place_order,
    ) -> None:
        """A buy fill adds a long position record to the book."""
        engine._current_price = lambda _symbol: 100.0  # mock price

        result = engine.place_order("BTC-USDT", "buy", 50.0)

        assert result["status"] == "ok"
        assert "BTC-USDT" in engine._positions
        fills = engine._positions["BTC-USDT"]
        assert len(fills) == 1
        # quantity = notional / price = 50 / 100 = 0.5
        assert fills[0]["quantity"] == pytest.approx(0.5)
        assert fills[0]["entry_price"] == pytest.approx(100.0)
        assert fills[0]["side"] == "long"

    def test_trade_log_records_fill(self, engine: PaperEngine, mock_place_order) -> None:
        """The trade log captures the fill details."""
        engine._current_price = lambda _symbol: 100.0

        engine.place_order("BTC-USDT", "buy", 50.0)

        assert len(engine._trade_log) == 1
        entry = engine._trade_log[0]
        assert entry["symbol"] == "BTC-USDT"
        assert entry["side"] == "buy"
        assert entry["notional"] == pytest.approx(50.0)

    def test_get_positions_returns_paper_position(
        self, engine: PaperEngine, mock_place_order,
    ) -> None:
        """get_positions returns correct PaperPosition objects after a fill."""
        engine._current_price = lambda _symbol: 100.0
        engine.place_order("BTC-USDT", "buy", 50.0)

        # Now change the current price to compute unrealized PnL.
        engine._current_price = lambda _symbol: 110.0

        positions = engine.get_positions()
        assert len(positions) == 1
        pos = positions[0]
        assert isinstance(pos, PaperPosition)
        assert pos.symbol == "BTC-USDT"
        assert pos.quantity == pytest.approx(0.5)  # 50 / 100
        assert pos.entry_price == pytest.approx(100.0)
        # unrealized = (current - entry) * qty - buy fee
        #            = (110 - 100) * 0.5 - 50 * 0.0008 = 4.96 (net of fees)
        assert pos.unrealized_pnl == pytest.approx(4.96)

    def test_get_positions_empty_when_no_fills(self, engine: PaperEngine) -> None:
        """No fills → empty positions list."""
        positions = engine.get_positions()
        assert positions == []


# ---------------------------------------------------------------------------
# 3. Order enforcement
# ---------------------------------------------------------------------------


class TestOrderEnforcement:
    """Verify notional cap enforcement and successful order placement."""

    def test_oversize_notional_rejected(self, engine: PaperEngine) -> None:
        """Notional exceeding max_order_notional_usd raises ValueError."""
        # Default max_order_notional_usd = 50.0
        with pytest.raises(ValueError, match="exceeds max"):
            engine.place_order("BTC-USDT", "buy", 100.0)

    def test_valid_notional_succeeds(
        self, engine: PaperEngine, mock_place_order,
    ) -> None:
        """A notional within the cap succeeds and returns an ok status."""
        engine._current_price = lambda _symbol: 100.0

        result = engine.place_order("BTC-USDT", "buy", 50.0)
        assert result["status"] == "ok"
        assert result["order_id"] == "test-123"

    def test_failed_order_does_not_update_positions(
        self, monkeypatch, tmp_path,
    ) -> None:
        """When the SDK returns an error, the position book is not updated."""
        def _fail_place_order(config, **kwargs):
            return {"status": "error", "error": "insufficient balance"}

        monkeypatch.setattr(
            "src.crypto_autopilot.paper_engine.okx_sdk.place_order",
            _fail_place_order,
        )
        engine = PaperEngine(config=AutopilotConfig(max_order_notional_usd=50.0), runtime_root=tmp_path)
        engine._current_price = lambda _symbol: 100.0

        result = engine.place_order("BTC-USDT", "buy", 50.0)
        assert result["status"] == "error"
        assert len(engine._positions.get("BTC-USDT", [])) == 0


# ---------------------------------------------------------------------------
# 4. Trade ledger persistence
# ---------------------------------------------------------------------------


class TestLedgerPersistence:
    """Verify fills land in the trade ledger and survive restarts."""

    def test_fill_is_written_to_ledger(
        self, engine: PaperEngine, mock_place_order, tmp_path,
    ) -> None:
        """A successful fill appends one paper record to the ledger."""
        from src.crypto_autopilot.trade_ledger import read_trade_records

        engine._current_price = lambda _symbol: 100.0
        engine.place_order("BTC-USDT", "buy", 50.0)

        records = read_trade_records(tmp_path, engine="paper")
        assert len(records) == 1
        record = records[0]
        assert record["engine"] == "paper"
        assert record["symbol"] == "BTC-USDT"
        assert record["side"] == "buy"
        assert record["quantity"] == pytest.approx(0.5)
        assert record["price"] == pytest.approx(100.0)
        assert record["notional"] == pytest.approx(50.0)

    def test_failed_order_writes_no_ledger(
        self, tmp_path, monkeypatch,
    ) -> None:
        """A rejected order never reaches the ledger."""
        from src.crypto_autopilot.trade_ledger import read_trade_records

        def _fail(config, **kwargs):
            return {"status": "error", "error": "insufficient balance"}

        monkeypatch.setattr(
            "src.crypto_autopilot.paper_engine.okx_sdk.place_order", _fail,
        )
        engine = PaperEngine(config=AutopilotConfig(max_order_notional_usd=50.0), runtime_root=tmp_path)
        engine._current_price = lambda _symbol: 100.0
        engine.place_order("BTC-USDT", "buy", 50.0)

        assert read_trade_records(tmp_path) == []

    def test_position_book_restored_from_ledger(
        self, tmp_path, mock_place_order,
    ) -> None:
        """A new engine replays persisted fills to rebuild positions."""
        first = PaperEngine(config=AutopilotConfig(max_order_notional_usd=50.0), runtime_root=tmp_path)
        first._current_price = lambda _symbol: 100.0
        first.place_order("BTC-USDT", "buy", 50.0)

        # Simulate a restart: a fresh engine over the same runtime root.
        restarted = PaperEngine(config=AutopilotConfig(max_order_notional_usd=50.0), runtime_root=tmp_path)
        restarted._current_price = lambda _symbol: 100.0

        assert "BTC-USDT" in restarted._positions
        fills = restarted._positions["BTC-USDT"]
        assert len(fills) == 1
        assert fills[0]["quantity"] == pytest.approx(0.5)
        assert fills[0]["entry_price"] == pytest.approx(100.0)
        # Trade log is restored too (audit completeness).
        assert len(restarted._trade_log) == 1

    def test_restore_does_not_duplicate_ledger_entries(
        self, tmp_path, mock_place_order,
    ) -> None:
        """Replaying fills at startup never re-appends to the ledger."""
        from src.crypto_autopilot.trade_ledger import read_trade_records

        first = PaperEngine(config=AutopilotConfig(max_order_notional_usd=50.0), runtime_root=tmp_path)
        first._current_price = lambda _symbol: 100.0
        first.place_order("BTC-USDT", "buy", 50.0)
        PaperEngine(config=AutopilotConfig(max_order_notional_usd=50.0), runtime_root=tmp_path)

        assert len(read_trade_records(tmp_path, engine="paper")) == 1

    def test_corrupt_ledger_degrades_to_cold_start(
        self, tmp_path, mock_place_order,
    ) -> None:
        """A broken ledger never raises — the engine cold-starts."""
        from src.crypto_autopilot.trade_ledger import autopilot_trades_path

        path = autopilot_trades_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{corrupt", encoding="utf-8")

        engine = PaperEngine(config=AutopilotConfig(max_order_notional_usd=50.0), runtime_root=tmp_path)
        assert engine._positions == {}
        assert engine._trade_log == []


# ---------------------------------------------------------------------------
# 4. Simulated fill mode (AUTOPILOT_PAPER_SIMULATED)
# ---------------------------------------------------------------------------


class TestSimulatedMode:
    """Local simulated fills need no broker round-trip or API key."""

    @staticmethod
    def _simulated_engine(tmp_path) -> PaperEngine:
        cfg = AutopilotConfig(paper_simulated=True, max_order_notional_usd=50.0)
        engine = PaperEngine(config=cfg, runtime_root=tmp_path)
        engine._current_price = lambda _symbol: 100.0
        return engine

    def test_simulated_fill_updates_ledger_and_positions(self, tmp_path) -> None:
        """A simulated fill behaves like a real demo fill."""
        from src.crypto_autopilot.trade_ledger import read_trade_records

        engine = self._simulated_engine(tmp_path)
        result = engine.place_order("BTC-USDT", "buy", 50.0)

        assert result["status"] == "ok"
        assert result["simulated"] is True
        assert result["fill_price"] == pytest.approx(100.0)
        fills = engine._positions["BTC-USDT"]
        assert len(fills) == 1
        assert fills[0]["quantity"] == pytest.approx(0.5)
        records = read_trade_records(tmp_path, engine="paper")
        assert len(records) == 1
        assert records[0]["symbol"] == "BTC-USDT"
        assert records[0]["side"] == "buy"
        assert records[0]["notional"] == pytest.approx(50.0)

    def test_simulated_fill_no_price_returns_error(self, tmp_path) -> None:
        """Missing market price fails closed without touching the ledger."""
        engine = self._simulated_engine(tmp_path)
        engine._current_price = lambda _symbol: None

        result = engine.place_order("ETH-USDT", "buy", 25.0)

        assert result["status"] == "error"
        assert result["simulated"] is True
        assert "no market price" in result["error"]
        assert engine._positions == {}

    def test_default_config_still_uses_sdk_path(self, tmp_path, monkeypatch) -> None:
        """Without the flag, orders go through the OKX SDK as before."""
        calls: list[dict] = []

        def _fake_place_order(config, **kwargs):
            calls.append(kwargs)
            return {"status": "ok", "order_id": "x"}

        monkeypatch.setattr(
            "src.crypto_autopilot.paper_engine.okx_sdk.place_order",
            _fake_place_order,
        )
        engine = PaperEngine(config=AutopilotConfig(max_order_notional_usd=50.0), runtime_root=tmp_path)

        result = engine.place_order("BTC-USDT", "buy", 50.0)

        assert result["status"] == "ok"
        assert "simulated" not in result
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# 5. Daily order counter (paper fills count toward the daily cap)
# ---------------------------------------------------------------------------


class TestDailyCounter:
    """Paper fills increment the persisted daily order counter."""

    @staticmethod
    def _simulated_engine(tmp_path, config=None) -> PaperEngine:
        cfg = config or AutopilotConfig(paper_simulated=True, max_order_notional_usd=50.0)
        engine = PaperEngine(config=cfg, runtime_root=tmp_path)
        engine._current_price = lambda _symbol: 100.0
        return engine

    def test_simulated_fill_increments_counter(self, tmp_path) -> None:
        """One simulated fill creates the counter file with today's UTC date."""
        engine = self._simulated_engine(tmp_path)
        result = engine.place_order("BTC-USDT", "buy", 50.0)

        assert result["status"] == "ok"
        assert engine._daily_counter.count_today() == 1
        counter_path = tmp_path / "daily_orders.json"
        assert counter_path.exists()
        import json

        raw = json.loads(counter_path.read_text(encoding="utf-8"))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert raw["date"] == today
        assert raw["count"] == 1

    def test_daily_limit_blocks_extra_order(self, tmp_path) -> None:
        """Once the daily cap is reached, further fills are rejected."""
        cfg = AutopilotConfig(paper_simulated=True, max_trades_per_day=1, max_order_notional_usd=50.0)
        engine = self._simulated_engine(tmp_path, config=cfg)

        first = engine.place_order("BTC-USDT", "buy", 50.0)
        assert first["status"] == "ok"

        second = engine.place_order("ETH-USDT", "buy", 50.0)
        assert second["status"] == "error"
        assert second["error"] == "daily order limit reached"
        assert engine._daily_counter.count_today() == 1
        assert "ETH-USDT" not in engine._positions

    def test_sdk_fill_increments_counter(self, tmp_path, monkeypatch) -> None:
        """The broker path counts toward the same cap."""
        monkeypatch.setattr(
            "src.crypto_autopilot.paper_engine.okx_sdk.place_order",
            lambda config, **kwargs: {"status": "ok", "order_id": "x"},
        )
        engine = PaperEngine(config=AutopilotConfig(max_order_notional_usd=50.0), runtime_root=tmp_path)

        result = engine.place_order("BTC-USDT", "buy", 50.0)

        assert result["status"] == "ok"
        assert engine._daily_counter.count_today() == 1


# ---------------------------------------------------------------------------
# 6. Position management (simulated close, exposure cap)
# ---------------------------------------------------------------------------


class TestPositionManagement:
    """Simulated closes settle locally; exposure caps block new buys."""

    @staticmethod
    def _simulated_engine(tmp_path, config=None) -> PaperEngine:
        cfg = config or AutopilotConfig(paper_simulated=True, max_order_notional_usd=50.0)
        engine = PaperEngine(config=cfg, runtime_root=tmp_path)
        engine._current_price = lambda _symbol: 100.0
        return engine

    def test_simulated_close_settles_position(self, tmp_path) -> None:
        """A simulated sell closes the book and records realized P&L."""
        from src.crypto_autopilot.trade_ledger import read_trade_records

        engine = self._simulated_engine(tmp_path)
        engine.place_order("BTC-USDT", "buy", 50.0)
        assert "BTC-USDT" in engine._positions

        result = engine.close_position("BTC-USDT")

        assert result["status"] == "ok"
        assert result["simulated"] is True
        # Flat price, but realized is net of fees: buy fee 50*0.0008 plus
        # sell fee 50*0.0008 = -0.08 (net-of-fee accounting).
        assert result["realized_pnl"] == pytest.approx(-0.08)
        assert engine._positions.get("BTC-USDT") == []
        records = read_trade_records(tmp_path, engine="paper")
        assert len(records) == 2
        assert records[0]["side"] == "sell"  # newest first
        # Fees are recorded on every fill (Phase 2: cost realism).
        assert records[0]["fee"] == pytest.approx(0.04)
        assert records[1]["fee"] == pytest.approx(0.04)

    def test_close_position_no_position_returns_error(self, tmp_path) -> None:
        engine = self._simulated_engine(tmp_path)
        result = engine.close_position("BTC-USDT")
        assert result["status"] == "error"
        assert "no open position" in result["error"]

    def test_exposure_cap_blocks_new_buy(self, tmp_path) -> None:
        """Open exposure + notional beyond the cap rejects the order."""
        cfg = AutopilotConfig(
            paper_simulated=True,
            max_total_exposure_usd=90.0,
            max_order_notional_usd=80.0,
        )
        engine = self._simulated_engine(tmp_path, config=cfg)

        first = engine.place_order("BTC-USDT", "buy", 80.0)
        assert first["status"] == "ok"

        second = engine.place_order("ETH-USDT", "buy", 50.0)
        assert second["status"] == "error"
        assert second["error"] == "exposure limit reached"
        assert "ETH-USDT" not in engine._positions

    def test_sell_exempt_from_daily_quota(self, tmp_path) -> None:
        """Closing a position works even at the daily order cap."""
        cfg = AutopilotConfig(paper_simulated=True, max_trades_per_day=1, max_order_notional_usd=50.0)
        engine = self._simulated_engine(tmp_path, config=cfg)

        assert engine.place_order("BTC-USDT", "buy", 50.0)["status"] == "ok"
        assert engine.place_order("ETH-USDT", "buy", 50.0)["status"] == "error"

        close = engine.close_position("BTC-USDT")
        assert close["status"] == "ok"
        assert engine._positions.get("BTC-USDT") == []

    def test_open_exposure_usd_sums_positions(self, tmp_path) -> None:
        engine = self._simulated_engine(tmp_path)
        engine.place_order("BTC-USDT", "buy", 50.0)
        engine.place_order("ETH-USDT", "buy", 30.0)

        assert engine.open_exposure_usd() == pytest.approx(80.0)
