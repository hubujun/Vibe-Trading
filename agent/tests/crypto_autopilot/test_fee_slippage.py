"""Phase 2 tests: fee accounting, net-of-fee P&L, and slippage measurement.

All network calls are stubbed — no OKX contact. The paper engine runs in
simulated mode so fills settle locally against a mocked price.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.crypto_autopilot.config import AutopilotConfig
from src.crypto_autopilot.paper_engine import PaperEngine
from src.crypto_autopilot.trade_ledger import (
    autopilot_slippage_path,
    read_slippage_records,
    read_trade_records,
)

__all__ = []


def _simulated_engine(tmp_path: Path, config=None) -> PaperEngine:
    # These tests focus on fee accounting, not the notional cap (which has
    # its own tests) — so lift the default cap to fit the $50 test orders.
    cfg = config or AutopilotConfig(
        paper_simulated=True, max_order_notional_usd=100.0,
    )
    engine = PaperEngine(config=cfg, runtime_root=tmp_path)
    engine._current_price = lambda _symbol: 100.0
    return engine


class TestFeeAccounting:
    def test_ledger_records_nonzero_fee(self, tmp_path: Path) -> None:
        """Every paper fill carries a fee = notional * fee_rate_taker."""
        engine = _simulated_engine(tmp_path)
        engine.place_order("BTC-USDT", "buy", 50.0)

        records = read_trade_records(tmp_path, engine="paper")
        assert len(records) == 1
        assert records[0]["fee"] == pytest.approx(50.0 * 0.0008)

    def test_custom_fee_rate_from_config(self, tmp_path: Path) -> None:
        """A configurable fee rate flows into the ledger fee."""
        cfg = AutopilotConfig(
            paper_simulated=True, fee_rate_taker=0.001,
            max_order_notional_usd=100.0,
        )
        engine = _simulated_engine(tmp_path, config=cfg)
        engine.place_order("BTC-USDT", "buy", 50.0)

        records = read_trade_records(tmp_path, engine="paper")
        assert records[0]["fee"] == pytest.approx(0.05)

    def test_realized_pnl_net_of_both_sides_fees(self, tmp_path: Path) -> None:
        """Closing at a profit nets out buy and sell fees from realized P&L.

        Buy 0.5 BTC @ 100 (fee 0.04), close @ 120 (sell notional 60, fee
        0.048): gross = (120-100)*0.5 = 10.0 → net = 10.0 - 0.04 - 0.048
        = 9.912.
        """
        engine = _simulated_engine(tmp_path)
        engine.place_order("BTC-USDT", "buy", 50.0)

        engine._current_price = lambda _symbol: 120.0
        result = engine.close_position("BTC-USDT")

        assert result["status"] == "ok"
        assert result["realized_pnl"] == pytest.approx(9.912)
        assert engine.compute_daily_pnl() == pytest.approx(9.912)

    def test_partial_close_prorates_buy_fee(self, tmp_path: Path) -> None:
        """Closing half a position charges half the stored buy fee plus the
        sell fee on the closed notional."""
        cfg = AutopilotConfig(
            paper_simulated=True, fee_rate_taker=0.0008,
            max_order_notional_usd=100.0,
        )
        engine = _simulated_engine(tmp_path, config=cfg)
        engine.place_order("BTC-USDT", "buy", 100.0)  # 1.0 BTC @ 100, fee 0.08

        # Sell half (0.5 BTC @ 110): buy fee share 0.04 + sell fee 0.044.
        realized = engine._apply_fill(
            "BTC-USDT", "sell", 110.0, 0.5, datetime.now(timezone.utc),
            tally_realized=True, fee=0.044,
        )
        expected = (110.0 - 100.0) * 0.5 - 0.04 - 0.044
        assert realized == pytest.approx(expected)
        # The remaining fill still carries the un-consumed fee share.
        assert engine._positions["BTC-USDT"][0]["quantity"] == pytest.approx(0.5)
        assert engine._positions["BTC-USDT"][0]["fee"] == pytest.approx(0.04)

    def test_ledger_restore_replays_fees(self, tmp_path: Path) -> None:
        """Restart replay keeps the stored fees on the position book so a
        later close nets them out correctly."""
        first = _simulated_engine(tmp_path)
        first.place_order("BTC-USDT", "buy", 50.0)

        restarted = _simulated_engine(tmp_path)
        assert restarted._positions["BTC-USDT"][0]["fee"] == pytest.approx(0.04)

        restarted._current_price = lambda _symbol: 120.0
        result = restarted.close_position("BTC-USDT")
        # buy fee 0.04 + sell fee 120*0.5*0.0008=0.048 → 10 - 0.088 = 9.912
        assert result["realized_pnl"] == pytest.approx(9.912)


class TestSlippageMeasurement:
    def test_slippage_record_written_on_price_move(self, tmp_path: Path) -> None:
        """Signal price differs from fill price → slippage.jsonl entry."""
        engine = _simulated_engine(tmp_path)

        # Establish a position at 100.0 (signal == fill, no record), then
        # move the fill price so the next fill's spread is measurable.
        engine.place_order("BTC-USDT", "buy", 50.0)
        engine._current_price = lambda _symbol: 100.05
        engine._record_fill("ETH-USDT", "buy", 25.0, signal_price=100.0)

        assert autopilot_slippage_path(tmp_path).exists()
        records = read_slippage_records(tmp_path)
        assert len(records) == 1
        assert records[0]["symbol"] == "ETH-USDT"
        assert records[0]["signal_price"] == pytest.approx(100.0)
        assert records[0]["bps"] == pytest.approx(5.0)  # +5 bps

    def test_no_slippage_record_when_prices_match(self, tmp_path: Path) -> None:
        """Identical signal and fill prices produce no record (zero noise)."""
        engine = _simulated_engine(tmp_path)
        engine.place_order("BTC-USDT", "buy", 50.0)

        assert not autopilot_slippage_path(tmp_path).exists()
        assert read_slippage_records(tmp_path) == []

    def test_avg_slippage_bps_reported(self, tmp_path: Path) -> None:
        """read_slippage_records supports averaging for the performance API."""
        from src.crypto_autopilot.trade_ledger import append_slippage_record

        for i, bps in enumerate((10.0, -2.0, 4.0)):
            append_slippage_record(
                tmp_path,
                symbol=f"PAIR-{i}",
                signal_price=100.0,
                fill_price=100.0 + bps / 10_000.0 * 100.0,
            )

        records = read_slippage_records(tmp_path)
        avg = sum(r["bps"] for r in records) / len(records)
        assert avg == pytest.approx(4.0)
