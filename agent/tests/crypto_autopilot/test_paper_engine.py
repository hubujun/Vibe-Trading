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
def engine() -> PaperEngine:
    """A PaperEngine with default config and no positions."""
    return PaperEngine(config=AutopilotConfig())


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
        # unrealized = (current - entry) * qty = (110 - 100) * 0.5 = 5.0
        assert pos.unrealized_pnl == pytest.approx(5.0)

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
        self, monkeypatch,
    ) -> None:
        """When the SDK returns an error, the position book is not updated."""
        def _fail_place_order(config, **kwargs):
            return {"status": "error", "error": "insufficient balance"}

        monkeypatch.setattr(
            "src.crypto_autopilot.paper_engine.okx_sdk.place_order",
            _fail_place_order,
        )
        engine = PaperEngine(config=AutopilotConfig())
        engine._current_price = lambda _symbol: 100.0

        result = engine.place_order("BTC-USDT", "buy", 50.0)
        assert result["status"] == "error"
        assert len(engine._positions.get("BTC-USDT", [])) == 0
