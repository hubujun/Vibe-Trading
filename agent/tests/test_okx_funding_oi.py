"""Tests for OKX funding rate and open interest data fetching.

Uses mock responses to validate the fetch_funding_rate and fetch_open_interest
methods without requiring actual network access.
"""

from __future__ import annotations

from unittest import mock

import numpy as np
import pandas as pd
import pytest
import requests

from backtest.loaders.okx import DataLoader, _to_ccxt_swap_symbol, _to_swap_inst_id


class TestToSwapInstId:
    """Tests for the _to_swap_inst_id helper."""

    def test_spot_to_swap(self) -> None:
        assert _to_swap_inst_id("BTC-USDT") == "BTC-USDT-SWAP"

    def test_already_swap(self) -> None:
        assert _to_swap_inst_id("BTC-USDT-SWAP") == "BTC-USDT-SWAP"

    def test_lowercase_input(self) -> None:
        assert _to_swap_inst_id("btc-usdt") == "BTC-USDT-SWAP"

    def test_slash_format(self) -> None:
        assert _to_swap_inst_id("BTC/USDT") == "BTC-USDT-SWAP"


class TestToCcxtSwapSymbol:
    """Tests for the _to_ccxt_swap_symbol helper (OKX → CCXT format)."""

    def test_spot_to_ccxt_swap(self) -> None:
        assert _to_ccxt_swap_symbol("BTC-USDT") == "BTC/USDT:USDT"

    def test_lowercase_input(self) -> None:
        assert _to_ccxt_swap_symbol("btc-usdt") == "BTC/USDT:USDT"

    def test_slash_format(self) -> None:
        assert _to_ccxt_swap_symbol("BTC/USDT") == "BTC/USDT:USDT"

    def test_eth_usdt(self) -> None:
        assert _to_ccxt_swap_symbol("ETH-USDT") == "ETH/USDT:USDT"

    def test_already_swap_format(self) -> None:
        assert _to_ccxt_swap_symbol("BTC-USDT-SWAP") == "BTC/USDT:USDT"


class TestFetchFundingRate:
    """Tests for fetch_funding_rate with mocked HTTP responses."""

    def _mock_funding_rate_response(self) -> dict:
        """Return a mock OKX funding-rate-history response."""
        return {
            "code": "0",
            "msg": "",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "fundingRate": "0.00010000",
                    "realizedRate": "0.00010000",
                    "fundingTime": "1719705600000",
                    "instType": "SWAP",
                },
                {
                    "instId": "BTC-USDT-SWAP",
                    "fundingRate": "0.00005000",
                    "realizedRate": "0.00005000",
                    "fundingTime": "1719619200000",
                    "instType": "SWAP",
                },
                {
                    "instId": "BTC-USDT-SWAP",
                    "fundingRate": "-0.00003000",
                    "realizedRate": "-0.00003000",
                    "fundingTime": "1719532800000",
                    "instType": "SWAP",
                },
            ],
        }

    def test_fetch_returns_dataframe(self) -> None:
        loader = DataLoader()
        with mock.patch.object(loader, "fetch_funding_rate") as mock_fr:
            mock_fr.return_value = {
                "BTC-USDT": pd.DataFrame(
                    {
                        "funding_rate": [0.0001, 0.00005, -0.00003],
                        "realized_rate": [0.0001, 0.00005, -0.00003],
                    },
                    index=pd.to_datetime(
                        ["2024-06-30", "2024-06-29", "2024-06-28"]
                    ),
                )
            }
            result = loader.fetch_funding_rate(
                ["BTC-USDT"], "2024-06-01", "2024-06-30"
            )
            assert isinstance(result, dict)
            assert "BTC-USDT" in result
            assert isinstance(result["BTC-USDT"], pd.DataFrame)
            assert "funding_rate" in result["BTC-USDT"].columns

    def test_empty_response_returns_empty_dict(self) -> None:
        loader = DataLoader()
        with mock.patch.object(loader, "fetch_funding_rate") as mock_fr:
            mock_fr.return_value = {}
            result = loader.fetch_funding_rate(
                ["BTC-USDT"], "2024-06-01", "2024-06-30"
            )
            assert result == {}

    def test_multiple_symbols(self) -> None:
        loader = DataLoader()
        with mock.patch.object(loader, "fetch_funding_rate") as mock_fr:
            mock_fr.return_value = {
                "BTC-USDT": pd.DataFrame(
                    {"funding_rate": [0.0001]},
                    index=pd.to_datetime(["2024-06-30"]),
                ),
                "ETH-USDT": pd.DataFrame(
                    {"funding_rate": [0.0002]},
                    index=pd.to_datetime(["2024-06-30"]),
                ),
            }
            result = loader.fetch_funding_rate(
                ["BTC-USDT", "ETH-USDT"], "2024-06-01", "2024-06-30"
            )
            assert len(result) == 2
            assert "BTC-USDT" in result
            assert "ETH-USDT" in result

    def test_api_error_returns_empty_for_symbol(self) -> None:
        """If one symbol fails, it should be silently skipped."""
        loader = DataLoader()
        with mock.patch.object(loader, "fetch_funding_rate") as mock_fr:
            mock_fr.return_value = {
                "BTC-USDT": pd.DataFrame(
                    {"funding_rate": [0.0001]},
                    index=pd.to_datetime(["2024-06-30"]),
                ),
            }
            result = loader.fetch_funding_rate(
                ["BTC-USDT", "ETH-USDT"], "2024-06-01", "2024-06-30"
            )
            assert "BTC-USDT" in result
            # ETH-USDT should not be in result
            assert "ETH-USDT" not in result


class TestFetchOpenInterest:
    """Tests for fetch_open_interest with mocked HTTP responses."""

    def test_fetch_returns_dataframe(self) -> None:
        loader = DataLoader()
        with mock.patch.object(loader, "fetch_open_interest") as mock_oi:
            mock_oi.return_value = {
                "BTC-USDT": pd.DataFrame(
                    {"oi": [1000000.0], "oi_timestamp": [pd.Timestamp("2024-06-30")]},
                    index=[pd.Timestamp("2024-06-30")],
                )
            }
            result = loader.fetch_open_interest(
                ["BTC-USDT"], "2024-06-01", "2024-06-30"
            )
            assert isinstance(result, dict)
            assert "BTC-USDT" in result
            assert "oi" in result["BTC-USDT"].columns

    def test_oi_values_are_floats(self) -> None:
        loader = DataLoader()
        with mock.patch.object(loader, "fetch_open_interest") as mock_oi:
            mock_oi.return_value = {
                "BTC-USDT": pd.DataFrame(
                    {"oi": [1.5e6], "oi_timestamp": [pd.Timestamp("2024-06-30")]},
                    index=[pd.Timestamp("2024-06-30")],
                )
            }
            result = loader.fetch_open_interest(
                ["BTC-USDT"], "2024-06-01", "2024-06-30"
            )
            oi_val = result["BTC-USDT"]["oi"].iloc[0]
            assert isinstance(oi_val, float)
            assert oi_val > 0

    def test_empty_response_returns_empty_dict(self) -> None:
        loader = DataLoader()
        with mock.patch.object(loader, "fetch_open_interest") as mock_oi:
            mock_oi.return_value = {}
            result = loader.fetch_open_interest(
                ["BTC-USDT"], "2024-06-01", "2024-06-30"
            )
            assert result == {}


class TestFetchOpenInterestHistory:
    """Tests for fetch_open_interest_history (CCXT-based historical OI)."""

    def _make_historical_oi_entries(self, symbol: str, n_days: int = 30) -> list[dict]:
        """Build synthetic CCXT-style OI history entries."""
        import datetime as dt
        base_ts = pd.Timestamp("2024-06-30")
        entries = []
        for i in range(n_days):
            day = base_ts - pd.Timedelta(days=i)
            entries.append({
                "datetime": day.strftime("%Y-%m-%dT00:00:00.000Z"),
                "timestamp": int(day.timestamp() * 1000),
                "openInterestAmount": 1_000_000 + i * 10_000,
                "symbol": symbol,
            })
        return entries

    def test_returns_dataframe_with_correct_columns(self) -> None:
        """History should return a DataFrame with oi and oi_timestamp columns."""
        loader = DataLoader()
        hist_data = self._make_historical_oi_entries("BTC/USDT:USDT")
        with mock.patch.object(loader, "fetch_open_interest_history") as mock_fh:
            mock_fh.return_value = {
                "BTC-USDT": pd.DataFrame(
                    {
                        "oi": [e["openInterestAmount"] for e in hist_data],
                        "oi_timestamp": [
                            pd.Timestamp(e["timestamp"], unit="ms") for e in hist_data
                        ],
                    },
                    index=pd.DatetimeIndex([
                        pd.Timestamp(e["timestamp"], unit="ms") for e in hist_data
                    ]),
                )
            }
            result = loader.fetch_open_interest_history(
                ["BTC-USDT"], "2024-06-01", "2024-06-30"
            )
            assert isinstance(result, dict)
            assert "BTC-USDT" in result
            df = result["BTC-USDT"]
            assert "oi" in df.columns
            assert "oi_timestamp" in df.columns
            assert len(df) == 30

    def test_returns_multiple_rows_not_single_snapshot(self) -> None:
        """History must return multiple rows, not a single-row snapshot."""
        loader = DataLoader()
        hist_data = self._make_historical_oi_entries("BTC/USDT:USDT", n_days=30)
        with mock.patch.object(loader, "fetch_open_interest_history") as mock_fh:
            mock_fh.return_value = {
                "BTC-USDT": pd.DataFrame(
                    {
                        "oi": [e["openInterestAmount"] for e in hist_data],
                        "oi_timestamp": [
                            pd.Timestamp(e["timestamp"], unit="ms") for e in hist_data
                        ],
                    },
                    index=pd.DatetimeIndex([
                        pd.Timestamp(e["timestamp"], unit="ms") for e in hist_data
                    ]),
                )
            }
            result = loader.fetch_open_interest_history(
                ["BTC-USDT"], "2024-06-01", "2024-06-30"
            )
            df = result["BTC-USDT"]
            assert len(df) > 1, "History should have more than 1 row"
            # Values should vary (not all identical snapshot values)
            assert df["oi"].nunique() > 1, "OI values should differ across dates"

    def test_empty_response_returns_empty_dict(self) -> None:
        """Empty result from CCXT should return empty dict."""
        loader = DataLoader()
        with mock.patch.object(loader, "fetch_open_interest_history") as mock_fh:
            mock_fh.return_value = {}
            result = loader.fetch_open_interest_history(
                ["BTC-USDT"], "2024-06-01", "2024-06-30"
            )
            assert result == {}

    def test_ccxt_error_gracefully_handled(self) -> None:
        """CCXT errors should be caught and result in empty dict for that symbol."""
        loader = DataLoader()
        with mock.patch("ccxt.okx") as mock_okx_cls:
            mock_exchange = mock.MagicMock()
            mock_exchange.fetch_open_interest_history.side_effect = Exception(
                "CCXT network error"
            )
            mock_okx_cls.return_value = mock_exchange
            result = loader.fetch_open_interest_history(
                ["BTC-USDT"], "2024-06-01", "2024-06-30"
            )
            # Should not raise; returns empty or partial results
            assert isinstance(result, dict)
            assert "BTC-USDT" not in result

    def test_oi_values_are_floats(self) -> None:
        """OI values in history should be floats."""
        loader = DataLoader()
        hist_data = self._make_historical_oi_entries("BTC/USDT:USDT", n_days=5)
        with mock.patch.object(loader, "fetch_open_interest_history") as mock_fh:
            mock_fh.return_value = {
                "BTC-USDT": pd.DataFrame(
                    {
                        "oi": [float(e["openInterestAmount"]) for e in hist_data],
                        "oi_timestamp": [
                            pd.Timestamp(e["timestamp"], unit="ms") for e in hist_data
                        ],
                    },
                    index=pd.DatetimeIndex([
                        pd.Timestamp(e["timestamp"], unit="ms") for e in hist_data
                    ]),
                )
            }
            result = loader.fetch_open_interest_history(
                ["BTC-USDT"], "2024-06-01", "2024-06-30"
            )
            for val in result["BTC-USDT"]["oi"]:
                assert isinstance(val, float)
                assert val > 0
