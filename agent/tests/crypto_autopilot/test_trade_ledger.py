"""Tests for the autopilot trade ledger (paper/live unified audit stream).

Covers append/read round-trips, engine/symbol filtering, newest-first
ordering, corrupt-line tolerance, and the Shadow-Account-compatible record
shape (``TradeRecord``-style keys).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.crypto_autopilot.trade_ledger import (
    autopilot_trades_path,
    read_trade_records,
    write_trade_record,
)

__all__ = []


def _write_raw(tmp_path: Path, line: str) -> None:
    """Append a raw (possibly corrupt) line to the ledger."""
    path = autopilot_trades_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


class TestLedgerWrite:
    def test_write_creates_file_and_record(self, tmp_path: Path) -> None:
        """A write creates the ledger and returns the exact record."""
        record = write_trade_record(
            tmp_path,
            engine="paper",
            symbol="BTC-USDT",
            side="buy",
            notional=50.0,
            quantity=0.5,
            price=100.0,
        )
        assert record is not None
        assert record["engine"] == "paper"
        assert record["market"] == "crypto"
        assert record["fee"] == 0.0
        path = autopilot_trades_path(tmp_path)
        assert path.is_file()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["symbol"] == "BTC-USDT"

    def test_write_appends_not_truncates(self, tmp_path: Path) -> None:
        """Consecutive writes accumulate records (append-only)."""
        for _ in range(3):
            write_trade_record(
                tmp_path, engine="live", symbol="ETH-USDT",
                side="buy", notional=10.0,
            )
        records = read_trade_records(tmp_path)
        assert len(records) == 3


class TestLedgerRead:
    def test_missing_ledger_returns_empty(self, tmp_path: Path) -> None:
        """No ledger file → empty list (never raises)."""
        assert read_trade_records(tmp_path) == []

    def test_read_is_newest_first(self, tmp_path: Path) -> None:
        """Records come back newest first."""
        write_trade_record(
            tmp_path, engine="paper", symbol="BTC-USDT", side="buy",
            notional=10.0, ts="2026-08-01T00:00:00+00:00",
        )
        write_trade_record(
            tmp_path, engine="paper", symbol="BTC-USDT", side="sell",
            notional=10.0, ts="2026-08-02T00:00:00+00:00",
        )
        records = read_trade_records(tmp_path)
        assert [r["side"] for r in records] == ["sell", "buy"]

    def test_filters_by_engine_and_symbol(self, tmp_path: Path) -> None:
        """Engine and symbol filters narrow the returned stream."""
        write_trade_record(
            tmp_path, engine="paper", symbol="BTC-USDT", side="buy", notional=1.0,
        )
        write_trade_record(
            tmp_path, engine="live", symbol="BTC-USDT", side="buy", notional=2.0,
        )
        write_trade_record(
            tmp_path, engine="live", symbol="ETH-USDT", side="sell", notional=3.0,
        )
        assert len(read_trade_records(tmp_path, engine="live")) == 2
        assert len(read_trade_records(tmp_path, symbol="btc-usdt")) == 2
        assert len(read_trade_records(tmp_path, engine="paper", symbol="ETH-USDT")) == 0

    def test_limit_clamps_result(self, tmp_path: Path) -> None:
        """The limit parameter caps how many records are returned."""
        for i in range(5):
            write_trade_record(
                tmp_path, engine="paper", symbol="BTC-USDT", side="buy",
                notional=float(i),
            )
        assert len(read_trade_records(tmp_path, limit=2)) == 2

    def test_corrupt_line_is_skipped(self, tmp_path: Path) -> None:
        """One bad line does not hide the rest of the ledger."""
        write_trade_record(
            tmp_path, engine="paper", symbol="BTC-USDT", side="buy", notional=1.0,
        )
        _write_raw(tmp_path, "{not-json")
        write_trade_record(
            tmp_path, engine="paper", symbol="BTC-USDT", side="sell", notional=2.0,
        )
        records = read_trade_records(tmp_path)
        assert len(records) == 2
        assert [r["side"] for r in records] == ["sell", "buy"]


class TestShadowCompatibility:
    def test_record_has_traderecord_keys(self, tmp_path: Path) -> None:
        """The ledger record carries the TradeRecord journal keys."""
        record = write_trade_record(
            tmp_path,
            engine="paper",
            symbol="BTC-USDT",
            side="buy",
            notional=50.0,
            quantity=0.5,
            price=100.0,
            realized_pnl=3.25,
            alpha_id="crypto_momentum_1h",
        )
        assert record is not None
        for key in ("ts", "symbol", "side", "quantity", "price", "fee", "market"):
            assert key in record
        assert record["realized_pnl"] == 3.25
        assert record["alpha_id"] == "crypto_momentum_1h"
