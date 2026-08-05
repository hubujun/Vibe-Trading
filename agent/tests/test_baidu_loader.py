"""Tests for Baidu Finance loader: daily-only A-share OHLCV."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.loaders import baidu_loader


@pytest.fixture(autouse=True)
def _bypass_cache(monkeypatch):
    """Replace cached_loader_fetch with a direct passthrough for all tests."""
    monkeypatch.setattr(
        baidu_loader,
        "cached_loader_fetch",
        lambda **kwargs: kwargs["fetch"](),
    )


def test_intraday_request_rejected(monkeypatch) -> None:
    """Baidu only serves daily bars — intraday should return empty."""
    calls: list[str] = []
    daily = pd.DataFrame(
        {
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [100.0],
        },
        index=pd.DatetimeIndex([pd.Timestamp("2026-01-05")]),
    )
    loader = baidu_loader.DataLoader()
    monkeypatch.setattr(
        loader,
        "_fetch_one",
        lambda code, start, end: calls.append(code) or daily,
    )

    result = loader.fetch(
        ["600519.SH"],
        "2026-01-01",
        "2026-01-31",
        interval="1m",
    )

    assert result == {}
    assert calls == []


def test_daily_request_still_works(monkeypatch) -> None:
    """Daily interval should proceed normally."""
    calls: list[str] = []
    daily = pd.DataFrame(
        {
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [100.0],
        },
        index=pd.DatetimeIndex([pd.Timestamp("2026-01-05")]),
    )
    loader = baidu_loader.DataLoader()
    monkeypatch.setattr(
        loader,
        "_fetch_one",
        lambda code, start, end: calls.append(code) or daily,
    )

    result = loader.fetch(
        ["600519.SH"],
        "2026-01-01",
        "2026-01-31",
        interval="1D",
    )

    assert "600519.SH" in result
    assert calls == ["600519.SH"]


def test_non_a_share_skipped() -> None:
    """Non-A-share codes should be silently skipped."""
    loader = baidu_loader.DataLoader()

    result = loader.fetch(
        ["AAPL", "BTC-USD", "00700.HK"],
        "2026-01-01",
        "2026-01-31",
    )

    assert result == {}


def test_to_baidu_code() -> None:
    """Verify symbol conversion to Baidu 6-digit format."""
    assert baidu_loader._to_baidu_code("600519.SH") == "600519"
    assert baidu_loader._to_baidu_code("000001.SZ") == "000001"
    assert baidu_loader._to_baidu_code("600519.sh") == "600519"
    assert baidu_loader._to_baidu_code("000001.sz") == "000001"


def test_is_a_share() -> None:
    """Verify A-share detection."""
    assert baidu_loader._is_a_share("600519.SH") is True
    assert baidu_loader._is_a_share("000001.SZ") is True
    assert baidu_loader._is_a_share("600519.sh") is True
    assert baidu_loader._is_a_share("AAPL") is False
    assert baidu_loader._is_a_share("00700.HK") is False


def test_loader_registered() -> None:
    """Verify baidu loader is registered in the global registry."""
    from backtest.loaders.registry import LOADER_REGISTRY, _ensure_registered
    _ensure_registered()
    assert "baidu" in LOADER_REGISTRY
    loader_cls = LOADER_REGISTRY["baidu"]
    assert loader_cls.name == "baidu"
    assert "a_share" in loader_cls.markets
    assert loader_cls.requires_auth is False
