"""Tests for the Options Lab analytics layer (vol surface + chain Greeks).

All Yahoo traffic is mocked at ``backtest.options_analytics.yahoo_client`` —
the client functions the module imports — so no test reaches a live endpoint.
The fixture chain mirrors the real ``optionChain.result[0]`` payload shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backtest import options_analytics as oa

__all__ = []


def _contract(symbol: str, strike: float, *, iv: float | None, last: float | None = None,
              bid: float | None = None, ask: float | None = None) -> dict:
    return {
        "contractSymbol": symbol,
        "strike": strike,
        "lastPrice": last,
        "bid": bid,
        "ask": ask,
        "volume": 100,
        "openInterest": 500,
        "impliedVolatility": iv,
        "inTheMoney": False,
    }


def _sample_chain(expiration: int = 1_764_512_640) -> dict:
    """A 5-strike chain around a 100.0 spot with a classic negative skew.

    Expiry sits ~182 days after the test reference instant, so the 25-delta
    window is reachable (strike 125 call IV 0.35, strike 87 put IV 0.45).
    """
    return {
        "expirationDates": [expiration, expiration + 604_800],
        "strikes": [87.0, 90.0, 100.0, 110.0, 125.0],
        "options": [
            {
                "expirationDate": expiration,
                "calls": [
                    _contract("SPY250601C00087000", 87.0, iv=0.30, last=15.0),
                    _contract("SPY250601C00090000", 90.0, iv=0.30, last=12.0),
                    _contract("SPY250601C00100000", 100.0, iv=0.20, last=5.0),
                    _contract("SPY250601C00110000", 110.0, iv=0.50, last=1.5),
                    _contract("SPY250601C00125000", 125.0, iv=0.35, last=0.8),
                ],
                "puts": [
                    _contract("SPY250601P00087000", 87.0, iv=0.45, last=0.9),
                    _contract("SPY250601P00090000", 90.0, iv=0.34, last=1.2),
                    _contract("SPY250601P00100000", 100.0, iv=0.21, last=4.8),
                    _contract("SPY250601P00110000", 110.0, iv=0.24, last=11.0),
                    _contract("SPY250601P00125000", 125.0, iv=0.40, last=26.0),
                ],
            }
        ],
    }


def _patch_yahoo(chain: dict | None = None, spot: float | None = 100.0):
    """Patch both Yahoo entry points used by the analytics layer.

    The options mock serves the chain for the nearest expiry and routes
    per-expiration requests to the matching options block (mirroring the real
    endpoint), so multi-expiry tests see distinct blocks.
    """
    chain = chain if chain is not None else _sample_chain()
    block_by_expiration = {
        int(block.get("expirationDate")): block
        for block in chain.get("options") or []
        if block.get("expirationDate") is not None
    }

    def _get_options(symbol: str, *, expiration=None):
        if expiration is None:
            return chain
        block = block_by_expiration.get(int(expiration))
        if block is None:
            return {"expirationDates": chain.get("expirationDates") or [], "options": []}
        return {
            "expirationDates": chain.get("expirationDates") or [],
            "options": [block],
        }

    def _get_quote(symbol: str, *, modules=None):
        if spot is None:
            return {}
        return {"price": {"regularMarketPrice": spot}}

    return (
        patch.object(oa.yahoo_client, "get_options", side_effect=_get_options),
        patch.object(oa.yahoo_client, "get_quote_summary", side_effect=_get_quote),
    )


#: Reference instant ~2 weeks before the fixture expiry (1_750_000_000).
NOW = datetime(2025, 6, 1, tzinfo=timezone.utc)


class TestVolSurface:
    def test_surface_shape_and_summaries(self) -> None:
        """One expiry: contracts carry moneyness/IV/deltas, ATM + skew derived."""
        patchers = _patch_yahoo()
        with patchers[0], patchers[1]:
            surface = oa.build_vol_surface("SPY", max_expirations=2, now=NOW)

        assert surface["ticker"] == "SPY"
        assert surface["spot"] == 100.0
        assert len(surface["expirations"]) == 1

        expiry = surface["expirations"][0]
        assert expiry["expiration"] == 1_764_512_640
        assert expiry["days_to_expiry"] > 0
        # ATM IV is the average of the strike nearest spot (100.0).
        assert expiry["atm_iv"] == pytest.approx(0.205, abs=1e-9)
        # Skew: 25-delta put IV - 25-delta call IV; puts price in the crash,
        # so a negative-skew market has positive skew (exact value pinned in
        # TestSummarizeSurfaceRows).
        assert expiry["skew"] is not None
        assert expiry["skew"] > 0

        by_strike = {row["strike"]: row for row in expiry["contracts"]}
        assert set(by_strike) == {87.0, 90.0, 100.0, 110.0, 125.0}
        row = by_strike[90.0]
        assert row["moneyness"] == pytest.approx(0.9, abs=1e-4)
        assert row["iv_call"] == pytest.approx(0.30)
        assert row["iv_put"] == pytest.approx(0.34)
        # OTM call delta is positive, OTM put delta negative.
        assert 0 < row["delta_call"] < 1
        assert -1 < row["delta_put"] < 0

    def test_surface_caps_expirations(self) -> None:
        """max_expirations is clamped to the module cap (request count)."""
        chain = _sample_chain()
        chain["expirationDates"] = [
            1_764_512_640 + i * 86_400 for i in range(10)
        ]
        patchers = _patch_yahoo(chain=chain)
        with patchers[0] as mock_options, patchers[1]:
            oa.build_vol_surface("SPY", max_expirations=99, now=NOW)
        # One nearest-expiry fetch plus one per capped expiration.
        assert mock_options.call_count == 1 + oa._MAX_SURFACE_EXPIRATIONS

    def test_surface_without_spot_degrades(self) -> None:
        """Missing spot → moneyness/atm_iv None but rows still present."""
        patchers = _patch_yahoo(spot=None)
        with patchers[0], patchers[1]:
            surface = oa.build_vol_surface("SPY", now=NOW)
        assert surface["spot"] is None
        expiry = surface["expirations"][0]
        assert expiry["atm_iv"] is None
        assert all(row["moneyness"] is None for row in expiry["contracts"])

    def test_surface_empty_chain(self) -> None:
        """No chain payload → empty expirations list (never raises)."""
        patchers = _patch_yahoo(chain={})
        with patchers[0], patchers[1]:
            surface = oa.build_vol_surface("SPY", now=NOW)
        assert surface["expirations"] == []

    def test_surface_skips_strikes_without_iv(self) -> None:
        """Contracts without IV are still listed; IV fields are None."""
        chain = _sample_chain()
        chain["options"][0]["calls"][0]["impliedVolatility"] = None
        patchers = _patch_yahoo(chain=chain)
        with patchers[0], patchers[1]:
            surface = oa.build_vol_surface("SPY", now=NOW)
        row = surface["expirations"][0]["contracts"][0]
        assert row["strike"] == 87.0
        assert row["iv_call"] is None
        assert row["delta_call"] is None


class TestChainGreeks:
    def test_greeks_ladder_shape_and_values(self) -> None:
        """Each contract gets BSM Greeks priced at its own IV."""
        patchers = _patch_yahoo()
        with patchers[0], patchers[1]:
            chain = oa.build_chain_greeks("SPY", now=NOW)

        assert chain["ticker"] == "SPY"
        assert chain["spot"] == 100.0
        assert chain["expiration"] == 1_764_512_640
        assert len(chain["contracts"]) == 10
        # Sorted by strike, and call before put within each strike.
        strikes = [c["strike"] for c in chain["contracts"]]
        assert strikes == sorted(strikes)
        types = [c["type"] for c in chain["contracts"]]
        for i in range(0, len(types), 2):
            assert types[i] == "call" and types[i + 1] == "put"

        atm_call = next(c for c in chain["contracts"] if c["type"] == "call" and c["strike"] == 100.0)
        # ATM call with IV 0.20, ~half a year to expiry: delta ≈ 0.5.
        assert atm_call["delta"] == pytest.approx(0.5, abs=0.1)
        assert atm_call["gamma"] is not None and atm_call["gamma"] > 0
        assert atm_call["theta"] is not None and atm_call["theta"] < 0
        assert atm_call["vega"] is not None and atm_call["vega"] > 0

        otm_put = next(c for c in chain["contracts"] if c["type"] == "put" and c["strike"] == 110.0)
        assert otm_put["delta"] < -0.5

    def test_greeks_without_iv_keep_nulls(self) -> None:
        """Unpriced contracts keep iv and Greeks None."""
        chain = _sample_chain()
        chain["options"][0]["calls"][2]["impliedVolatility"] = None
        patchers = _patch_yahoo(chain=chain)
        with patchers[0], patchers[1]:
            result = oa.build_chain_greeks("SPY", now=NOW)
        missing = next(c for c in result["contracts"] if c["strike"] == 100.0 and c["type"] == "call")
        assert missing["iv"] is None
        assert missing["delta"] is None

    def test_greeks_empty_chain(self) -> None:
        """No options block → empty contracts list."""
        patchers = _patch_yahoo(chain={"expirationDates": [1_750_000_000]})
        with patchers[0], patchers[1]:
            result = oa.build_chain_greeks("SPY", now=NOW)
        assert result["contracts"] == []
        assert result["expiration"] is None

    def test_expiration_passthrough(self) -> None:
        """The requested expiration reaches the yahoo client."""
        patchers = _patch_yahoo()
        with patchers[0] as mock_options, patchers[1]:
            oa.build_chain_greeks("SPY", expiration=1_750_604_800, now=NOW)
        _, kwargs = mock_options.call_args
        assert kwargs["expiration"] == 1_750_604_800


class TestFetchSpot:
    def test_parses_regular_market_price(self) -> None:
        with patch.object(
            oa.yahoo_client, "get_quote_summary",
            return_value={"price": {"regularMarketPrice": 512.34}},
        ):
            assert oa.fetch_spot("SPY") == pytest.approx(512.34)

    def test_missing_quote_returns_none(self) -> None:
        with patch.object(oa.yahoo_client, "get_quote_summary", return_value={}):
            assert oa.fetch_spot("SPY") is None

    def test_upstream_error_returns_none(self) -> None:
        with patch.object(
            oa.yahoo_client, "get_quote_summary", side_effect=RuntimeError("boom")
        ):
            assert oa.fetch_spot("SPY") is None


class TestSummarizeSurfaceRows:
    def test_atm_iv_picks_nearest_strike(self) -> None:
        rows = [
            {"strike": 90.0, "iv_call": 0.30, "iv_put": 0.34, "delta_call": 0.9, "delta_put": -0.1},
            {"strike": 100.0, "iv_call": 0.20, "iv_put": 0.21, "delta_call": 0.5, "delta_put": -0.5},
            {"strike": 110.0, "iv_call": 0.16, "iv_put": 0.24, "delta_call": 0.1, "delta_put": -0.9},
        ]
        summary = oa.summarize_surface_rows(rows, spot=101.0)
        assert summary["atm_iv"] == pytest.approx(0.205)
        # 25-delta window: put at 110 (|−0.9| is far), call at 90 (0.9 far) —
        # no contract lands in [0.2, 0.3], so skew is None for this row set.
        assert summary["skew"] is None

    def test_skew_from_25_delta_contracts(self) -> None:
        rows = [
            {"strike": 90.0, "iv_call": 0.26, "iv_put": 0.36, "delta_call": 0.25, "delta_put": -0.25},
            {"strike": 100.0, "iv_call": 0.20, "iv_put": 0.21, "delta_call": 0.5, "delta_put": -0.5},
        ]
        summary = oa.summarize_surface_rows(rows, spot=100.0)
        assert summary["skew"] == pytest.approx(0.10)  # 0.36 - 0.26

    def test_empty_rows(self) -> None:
        assert oa.summarize_surface_rows([], spot=100.0) == {"atm_iv": None, "skew": None}
