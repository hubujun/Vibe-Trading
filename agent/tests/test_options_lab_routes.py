"""Tests for the Options Lab HTTP routes (vol surface + Greeks ladder).

All Yahoo traffic is mocked at ``backtest.options_analytics.yahoo_client``,
so no test ever reaches a live endpoint. The analytics layer itself is
covered by ``test_options_analytics.py``; these tests only pin the HTTP
contract: status codes, envelope shape, and error mapping.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import api_server
from backtest import options_analytics as oa

__all__ = []


def _sample_chain(expiration: int = 1_801_958_400) -> dict:
    return {
        "expirationDates": [expiration, expiration + 604_800],
        "strikes": [87.0, 90.0, 100.0, 110.0, 125.0],
        "options": [
            {
                "expirationDate": expiration,
                "calls": [
                    {"contractSymbol": "SPY250601C00087000", "strike": 87.0,
                     "lastPrice": 15.0, "bid": 14.9, "ask": 15.1, "volume": 100,
                     "openInterest": 500, "impliedVolatility": 0.30, "inTheMoney": True},
                    {"contractSymbol": "SPY250601C00090000", "strike": 90.0,
                     "lastPrice": 12.0, "bid": 11.9, "ask": 12.1, "volume": 100,
                     "openInterest": 500, "impliedVolatility": 0.30, "inTheMoney": True},
                    {"contractSymbol": "SPY250601C00100000", "strike": 100.0,
                     "lastPrice": 5.0, "bid": 4.9, "ask": 5.1, "volume": 200,
                     "openInterest": 800, "impliedVolatility": 0.20, "inTheMoney": False},
                    {"contractSymbol": "SPY250601C00110000", "strike": 110.0,
                     "lastPrice": 1.5, "bid": 1.4, "ask": 1.6, "volume": 50,
                     "openInterest": 300, "impliedVolatility": 0.50, "inTheMoney": False},
                    {"contractSymbol": "SPY250601C00125000", "strike": 125.0,
                     "lastPrice": 0.8, "bid": 0.7, "ask": 0.9, "volume": 30,
                     "openInterest": 200, "impliedVolatility": 0.35, "inTheMoney": False},
                ],
                "puts": [
                    {"contractSymbol": "SPY250601P00087000", "strike": 87.0,
                     "lastPrice": 0.9, "bid": 0.8, "ask": 1.0, "volume": 40,
                     "openInterest": 250, "impliedVolatility": 0.45, "inTheMoney": False},
                    {"contractSymbol": "SPY250601P00090000", "strike": 90.0,
                     "lastPrice": 1.2, "bid": 1.1, "ask": 1.3, "volume": 50,
                     "openInterest": 300, "impliedVolatility": 0.34, "inTheMoney": False},
                    {"contractSymbol": "SPY250601P00100000", "strike": 100.0,
                     "lastPrice": 4.8, "bid": 4.7, "ask": 4.9, "volume": 60,
                     "openInterest": 400, "impliedVolatility": 0.21, "inTheMoney": False},
                    {"contractSymbol": "SPY250601P00110000", "strike": 110.0,
                     "lastPrice": 11.0, "bid": 10.9, "ask": 11.1, "volume": 70,
                     "openInterest": 450, "impliedVolatility": 0.24, "inTheMoney": True},
                    {"contractSymbol": "SPY250601P00125000", "strike": 125.0,
                     "lastPrice": 26.0, "bid": 25.9, "ask": 26.1, "volume": 80,
                     "openInterest": 500, "impliedVolatility": 0.40, "inTheMoney": True},
                ],
            }
        ],
    }


def _patch_yahoo(chain: dict | None = None) -> tuple:
    """Patch both Yahoo entry points; per-expiration requests route to the
    matching block so multi-expiry fetches see distinct data."""
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
        return {"expirationDates": chain.get("expirationDates") or [], "options": [block]}

    return (
        patch.object(oa.yahoo_client, "get_options", side_effect=_get_options),
        patch.object(
            oa.yahoo_client,
            "get_quote_summary",
            return_value={"price": {"regularMarketPrice": 100.0}},
        ),
    )


def _client() -> TestClient:
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


class TestSurfaceEndpoint:
    def test_surface_returns_envelope(self) -> None:
        """A 200 with the surface shape; summaries are derived server-side."""
        patchers = _patch_yahoo()
        with patchers[0], patchers[1]:
            response = _client().get("/api/options-lab/surface", params={"ticker": "SPY"})

        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "SPY"
        assert body["spot"] == 100.0
        assert len(body["expirations"]) == 1
        expiry = body["expirations"][0]
        assert expiry["atm_iv"] is not None
        assert expiry["skew"] is not None
        assert len(expiry["contracts"]) == 5
        assert {"strike", "moneyness", "iv_call", "iv_put", "delta_call", "delta_put"} <= set(
            expiry["contracts"][0]
        )

    def test_surface_missing_ticker_is_422(self) -> None:
        """A missing/blank ticker is rejected by the query contract."""
        response = _client().get("/api/options-lab/surface")
        assert response.status_code == 422

    def test_surface_upstream_failure_is_502(self) -> None:
        """A Yahoo failure surfaces as a 502 with a readable detail."""
        with patch.object(
            oa.yahoo_client, "get_options", side_effect=RuntimeError("HTTP 429 banned")
        ):
            response = _client().get("/api/options-lab/surface", params={"ticker": "SPY"})

        assert response.status_code == 502
        assert "options surface fetch failed" in response.json()["detail"]


class TestChainEndpoint:
    def test_chain_returns_greeks_ladder(self) -> None:
        """A 200 with per-contract Greeks priced at each contract's IV."""
        patchers = _patch_yahoo()
        with patchers[0], patchers[1]:
            response = _client().get(
                "/api/options-lab/chain",
                params={"ticker": "SPY", "expiration": 1_801_958_400},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["spot"] == 100.0
        assert body["expiration"] == 1_801_958_400
        assert len(body["contracts"]) == 10
        contract = body["contracts"][0]
        for key in ("type", "strike", "iv", "bid", "ask", "last", "open_interest",
                    "volume", "delta", "gamma", "theta", "vega"):
            assert key in contract

    def test_chain_missing_ticker_is_422(self) -> None:
        response = _client().get("/api/options-lab/chain")
        assert response.status_code == 422

    def test_chain_upstream_failure_is_502(self) -> None:
        with patch.object(
            oa.yahoo_client, "get_options", side_effect=RuntimeError("boom")
        ):
            response = _client().get("/api/options-lab/chain", params={"ticker": "SPY"})

        assert response.status_code == 502
        assert "options chain fetch failed" in response.json()["detail"]
