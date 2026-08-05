"""Tests for the multi-symbol crypto panel loader (LAO-15)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pandas as pd

from src.tools import alpha_bench_tool as abt


def test_crypto_symbols_default():
    """Default list must include the 8 major pairs."""
    syms = abt._crypto_symbols()
    assert len(syms) == 8
    assert "BTC-USDT" in syms
    assert "ETH-USDT" in syms
    assert "SOL-USDT" in syms
    assert "BNB-USDT" in syms
    assert "XRP-USDT" in syms
    assert "DOGE-USDT" in syms
    assert "ADA-USDT" in syms
    assert "AVAX-USDT" in syms


def test_crypto_symbols_env_override(monkeypatch):
    """CRYPTO_SYMBOLS env var should override the default list."""
    monkeypatch.setenv("CRYPTO_SYMBOLS", "BTC-USDT,ETH-USDT,DOT-USDT")
    syms = abt._crypto_symbols()
    assert syms == ["BTC-USDT", "ETH-USDT", "DOT-USDT"]


def test_crypto_symbols_env_empty_respects_default(monkeypatch):
    """Empty env var falls back to default."""
    monkeypatch.setenv("CRYPTO_SYMBOLS", "")
    syms = abt._crypto_symbols()
    assert len(syms) == 8


def test_universe_tag_crypto():
    """'crypto' and 'btc-usdt' both map to 'crypto' tag."""
    assert abt._UNIVERSE_TAG["crypto"] == "crypto"
    assert abt._UNIVERSE_TAG["btc-usdt"] == "crypto"


def test_universe_tag_contains_all_expected_keys():
    """All expected universes are registered."""
    assert set(abt._UNIVERSE_TAG.keys()) == {"csi300", "sp500", "btc-usdt", "crypto"}


def test_load_crypto_panel_multi_symbol(monkeypatch):
    """_load_crypto_panel should fetch all configured symbols."""
    # Override symbols to a known small set for fast testing.
    monkeypatch.setattr(abt, "_crypto_symbols", lambda: ["BTC-USDT", "ETH-USDT"])

    # Build fake OHLCV DataFrames
    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    btc = pd.DataFrame(
        {"open": [100]*5, "high": [110]*5, "low": [90]*5, "close": [105]*5, "volume": [1e6]*5},
        index=idx,
    )
    eth = pd.DataFrame(
        {"open": [10]*5, "high": [11]*5, "low": [9]*5, "close": [10.5]*5, "volume": [2e6]*5},
        index=idx,
    )

    # Patch resolve_loader to return a mock that returns our data
    mock_loader = MagicMock()
    mock_loader.fetch.return_value = {"BTC-USDT": btc, "ETH-USDT": eth}

    monkeypatch.setattr(
        "backtest.loaders.registry.resolve_loader", lambda market: mock_loader
    )

    # Patch _retry to call the fn directly
    def _fake_retry(fn):
        return fn()
    monkeypatch.setattr(abt, "_retry", _fake_retry)

    panel = abt._load_crypto_panel("2020-01-01", "2020-01-10")

    # Should have close, open, high, low, volume, vwap
    assert "close" in panel
    assert "volume" in panel
    assert "vwap" in panel

    # close should be multi-column
    close_df = panel["close"]
    assert close_df.shape[1] == 2
    assert "BTC-USDT" in close_df.columns
    assert "ETH-USDT" in close_df.columns


def test_load_universe_panel_routes_crypto(monkeypatch):
    """_load_universe_panel should route 'crypto' to _load_crypto_panel."""
    fake_panel = {
        "close": pd.DataFrame({"A": [1.0]}, index=pd.DatetimeIndex(["2020-01-01"])),
    }

    import src.tools.alpha_bench_tool as abt2
    called_with = []

    def fake_load(start, end):
        called_with.append((start, end))
        return fake_panel

    monkeypatch.setattr(abt2, "_load_crypto_panel", fake_load)

    # Clear cache to avoid loading from disk
    result = abt2._load_universe_panel("crypto", "2020-2020", use_cache=False)
    assert called_with == [("2020-01-01", "2020-12-31")]
    assert result is fake_panel


def test_load_universe_panel_routes_btc_usdt_legacy(monkeypatch):
    """_load_universe_panel should route 'btc-usdt' to _load_crypto_panel (backward compat)."""
    fake_panel = {
        "close": pd.DataFrame({"A": [1.0]}, index=pd.DatetimeIndex(["2020-01-01"])),
    }

    import src.tools.alpha_bench_tool as abt2
    called_with = []

    def fake_load(start, end):
        called_with.append((start, end))
        return fake_panel

    monkeypatch.setattr(abt2, "_load_crypto_panel", fake_load)

    result = abt2._load_universe_panel("btc-usdt", "2020-2020", use_cache=False)
    assert called_with == [("2020-01-01", "2020-12-31")]
    assert result is fake_panel


def test_load_crypto_panel_dune_unavailable_logs_warning(caplog, monkeypatch):
    """When DuneLoader is unavailable (no DUNE_API_KEY), a user-visible
    warning with guidance should be logged at WARNING level."""
    import logging

    # Override symbols to a known small set for fast testing.
    monkeypatch.setattr(abt, "_crypto_symbols", lambda: ["BTC-USDT", "ETH-USDT"])

    # Build fake OHLCV DataFrames
    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    btc = pd.DataFrame(
        {"open": [100] * 5, "high": [110] * 5, "low": [90] * 5,
         "close": [105] * 5, "volume": [1e6] * 5},
        index=idx,
    )
    eth = pd.DataFrame(
        {"open": [10] * 5, "high": [11] * 5, "low": [9] * 5,
         "close": [10.5] * 5, "volume": [2e6] * 5},
        index=idx,
    )

    # Patch resolve_loader for OHLCV
    mock_loader = MagicMock()
    mock_loader.fetch.return_value = {"BTC-USDT": btc, "ETH-USDT": eth}
    monkeypatch.setattr(
        "backtest.loaders.registry.resolve_loader", lambda market: mock_loader
    )

    # Patch _retry
    def _fake_retry(fn):
        return fn()
    monkeypatch.setattr(abt, "_retry", _fake_retry)

    # Patch DuneLoader to be unavailable
    mock_dune = MagicMock()
    mock_dune.is_available.return_value = False
    monkeypatch.setattr(
        "backtest.loaders.dune_loader.DuneLoader", lambda: mock_dune
    )

    # Also ensure DUNE_API_KEY is unset
    monkeypatch.delenv("DUNE_API_KEY", raising=False)

    # Reset the once-only flag so this test is independent
    abt._dune_key_warned = False

    caplog.set_level(logging.WARNING, logger="src.tools.alpha_bench_tool")

    panel = abt._load_crypto_panel("2020-01-01", "2020-01-10")

    # Should still produce a valid panel
    assert "close" in panel

    # Should log a WARNING with Dune guidance (not just info)
    dune_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "DUNE_API_KEY" in r.message
    ]
    assert len(dune_warnings) >= 1, (
        "Expected a WARNING-level log about DUNE_API_KEY guidance, "
        f"got records: {[(r.levelname, r.message) for r in caplog.records]}"
    )


def test_dune_key_warning_only_once(caplog):
    """_warn_dune_key_missing_once() should warn only on first call,
    then stay silent on subsequent calls."""
    import logging

    caplog.set_level(logging.WARNING, logger="src.tools.alpha_bench_tool")

    import src.tools.alpha_bench_tool as abt2

    # Save and reset the warning flag for a clean test
    saved = abt2._dune_key_warned
    abt2._dune_key_warned = False

    # First call — should warn
    abt2._warn_dune_key_missing_once()
    assert len(caplog.records) == 1
    assert "DUNE_API_KEY" in caplog.records[0].message

    caplog.clear()

    # Second call — should be silent
    abt2._warn_dune_key_missing_once()
    assert len(caplog.records) == 0

    caplog.clear()

    # Third call — still silent
    abt2._warn_dune_key_missing_once()
    assert len(caplog.records) == 0

    # Restore the original flag state
    abt2._dune_key_warned = saved


# ---------------------------------------------------------------------------
# Tests for _dune_api_key() graceful degradation (LAO-29)
# ---------------------------------------------------------------------------


def test_dune_api_key_returns_none_when_unset(monkeypatch):
    """_dune_api_key() returns None when DUNE_API_KEY is not set."""
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    from backtest.loaders.dune_loader import _dune_api_key
    assert _dune_api_key() is None


def test_dune_api_key_returns_none_when_empty(monkeypatch):
    """_dune_api_key() returns None when DUNE_API_KEY is empty string."""
    monkeypatch.setenv("DUNE_API_KEY", "")
    from backtest.loaders.dune_loader import _dune_api_key
    assert _dune_api_key() is None


def test_dune_api_key_returns_none_when_whitespace_only(monkeypatch):
    """_dune_api_key() returns None when DUNE_API_KEY is whitespace only."""
    monkeypatch.setenv("DUNE_API_KEY", "   ")
    from backtest.loaders.dune_loader import _dune_api_key
    assert _dune_api_key() is None


def test_dune_api_key_returns_key_when_set(monkeypatch):
    """_dune_api_key() returns the key when DUNE_API_KEY is properly set."""
    monkeypatch.setenv("DUNE_API_KEY", "test-key-123")
    from backtest.loaders.dune_loader import _dune_api_key
    assert _dune_api_key() == "test-key-123"


def test_dune_headers_raises_when_key_missing(monkeypatch):
    """_dune_headers() raises RuntimeError when DUNE_API_KEY is not set."""
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    import pytest
    from backtest.loaders.dune_loader import _dune_headers
    with pytest.raises(RuntimeError, match="DUNE_API_KEY not set"):
        _dune_headers()


def test_dune_loader_is_available_false_when_key_missing(monkeypatch):
    """DuneLoader.is_available() returns False when DUNE_API_KEY is missing."""
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    from backtest.loaders.dune_loader import DuneLoader
    loader = DuneLoader()
    assert loader.is_available() is False


def test_dune_loader_is_available_false_when_key_empty(monkeypatch):
    """DuneLoader.is_available() returns False when DUNE_API_KEY is empty string."""
    monkeypatch.setenv("DUNE_API_KEY", "")
    from backtest.loaders.dune_loader import DuneLoader
    loader = DuneLoader()
    assert loader.is_available() is False


def test_dune_loader_is_available_true_when_key_set(monkeypatch):
    """DuneLoader.is_available() returns True when DUNE_API_KEY is configured."""
    monkeypatch.setenv("DUNE_API_KEY", "test-key-123")
    from backtest.loaders.dune_loader import DuneLoader
    loader = DuneLoader()
    assert loader.is_available() is True
