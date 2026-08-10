"""Tests for the incremental parquet history store.

Covers full/incremental sync, de-duplication, window reads, panel assembly,
and corruption tolerance. All network calls are stubbed — no OKX contact.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.crypto_autopilot.history_store import HistoryStore, _merge_bars

__all__ = []


def _bars(n: int, start: str = "2026-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": 1.0,
        },
        index=idx,
    )


class TestMergeBars:
    def test_concatenates_and_sorts(self) -> None:
        merged = _merge_bars(_bars(3, "2026-01-01"), _bars(3, "2026-01-03"))
        assert len(merged) == 6
        assert merged.index.is_monotonic_increasing

    def test_dedup_keeps_latest(self) -> None:
        existing = _bars(5, "2026-01-01")
        fresh = _bars(5, "2026-01-01")
        fresh["close"] = 999.0
        merged = _merge_bars(existing, fresh)
        assert len(merged) == 5
        assert (merged["close"] == 999.0).all()

    def test_tz_aware_index_normalised(self) -> None:
        idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
        aware = pd.DataFrame(
            {"close": [1.0, 2.0, 3.0], "open": 1.0, "high": 1.0, "low": 1.0, "volume": 1.0},
            index=idx,
        )
        merged = _merge_bars(pd.DataFrame(), aware)
        assert merged.index.tz is None

    def test_empty_inputs(self) -> None:
        assert _merge_bars(pd.DataFrame(), pd.DataFrame()).empty


class TestHistoryStore:
    def test_ensure_history_fetches_and_persists(self, tmp_path: Path, monkeypatch) -> None:
        """A cold store fetches the full range and saves parquet."""
        fetched = _bars(24 * 30)

        def _fake_fetch(self, codes, start_date, end_date, *, interval="1H", fields=None, prefer_history=None):
            return {codes[0]: fetched}

        monkeypatch.setattr(
            "backtest.loaders.okx.DataLoader.fetch", _fake_fetch,
        )
        store = HistoryStore(root=tmp_path)

        df = store.ensure_history("BTC-USDT", period="1h", days=30)

        assert len(df) == 24 * 30
        assert store.path_for("BTC-USDT", "1h").exists()
        # Second call reads from disk, no re-fetch needed (frame same length).
        df2 = store.ensure_history("BTC-USDT", period="1h", days=30)
        assert len(df2) == len(df)

    def test_ensure_history_merges_incrementally(self, tmp_path: Path, monkeypatch) -> None:
        """Existing data plus a fetch gap is merged without duplicates."""
        store = HistoryStore(root=tmp_path)
        store._save(_bars(10, "2026-01-01"), "BTC-USDT", "1h")

        # Fetch returns 10 overlapping + 5 new bars.
        fetched = _merge_bars(
            _bars(10, "2026-01-01"), _bars(5, "2026-01-10"),
        )

        def _fake_fetch(self, codes, start_date, end_date, *, interval="1H", fields=None, prefer_history=None):
            return {codes[0]: fetched}

        monkeypatch.setattr(
            "backtest.loaders.okx.DataLoader.fetch", _fake_fetch,
        )

        df = store.ensure_history("BTC-USDT", period="1h", days=30)

        assert len(df) == 15
        assert df.index.is_monotonic_increasing

    def test_append_latest_adds_delta(self, tmp_path: Path, monkeypatch) -> None:
        """append_latest merges fresh live bars and reports the delta."""
        store = HistoryStore(root=tmp_path)
        store._save(_bars(5, "2026-01-01"), "ETH-USDT", "1h")

        def _fake_fetch_bars(self, symbol, period="1d", limit=90):
            return _bars(8, "2026-01-01")

        monkeypatch.setattr(
            "src.crypto_autopilot.market_feed.MarketFeed.fetch_bars",
            _fake_fetch_bars,
        )

        added = store.append_latest(["ETH-USDT"], period="1h", limit=8)

        assert added["ETH-USDT"] == 3
        assert len(store.get_window("ETH-USDT", period="1h", bars=100)) == 8

    def test_append_latest_failure_reports_minus_one(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A failing live feed is logged and reported, never raised."""

        def _raise(self, symbol, period="1d", limit=90):
            raise RuntimeError("feed down")

        monkeypatch.setattr(
            "src.crypto_autopilot.market_feed.MarketFeed.fetch_bars", _raise,
        )
        store = HistoryStore(root=tmp_path)

        added = store.append_latest(["BTC-USDT"], period="1h")

        assert added["BTC-USDT"] == -1

    def test_get_window_returns_tail(self, tmp_path: Path) -> None:
        store = HistoryStore(root=tmp_path)
        store._save(_bars(50, "2026-01-01"), "BTC-USDT", "1h")

        window = store.get_window("BTC-USDT", period="1h", bars=10)

        assert len(window) == 10
        # 50 hourly bars starting 2026-01-01 00:00 end at 2026-01-03 01:00.
        assert window.index[-1] == pd.Timestamp("2026-01-03 01:00")
        assert window.index[0] == pd.Timestamp("2026-01-02 16:00")

    def test_get_panel_assembles_wide_frames(self, tmp_path: Path) -> None:
        store = HistoryStore(root=tmp_path)
        store._save(_bars(20, "2026-01-01"), "BTC-USDT", "1h")
        store._save(_bars(20, "2026-01-01"), "ETH-USDT", "1h")

        panel = store.get_panel(["BTC-USDT", "ETH-USDT"], period="1h", bars=20)

        assert "close" in panel
        assert set(panel["close"].columns) == {"BTC-USDT", "ETH-USDT"}
        assert len(panel["close"]) == 20

    def test_get_panel_empty_without_data(self, tmp_path: Path) -> None:
        store = HistoryStore(root=tmp_path)
        assert store.get_panel(["BTC-USDT"], period="1h", bars=20) == {}

    def test_corrupt_file_treated_as_empty(self, tmp_path: Path) -> None:
        store = HistoryStore(root=tmp_path)
        path = store.path_for("BTC-USDT", "1h")
        path.write_text("not a parquet file", encoding="utf-8")

        df = store.get_window("BTC-USDT", period="1h", bars=10)

        assert df.empty
        assert store.latest_ts("BTC-USDT", "1h") is None

    def test_round_trip_through_parquet(self, tmp_path: Path) -> None:
        store = HistoryStore(root=tmp_path)
        store._save(_bars(10, "2026-01-01"), "SOL-USDT", "1h")

        loaded = store.get_window("SOL-USDT", period="1h", bars=10)

        assert isinstance(loaded.index, pd.DatetimeIndex)
        assert list(loaded.columns) == ["open", "high", "low", "close", "volume"]
        assert store.latest_ts("SOL-USDT", "1h") == pd.Timestamp("2026-01-01 09:00")
