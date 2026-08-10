"""Options Lab HTTP routes — vol surface + Greeks dashboard backend.

Mounted by ``agent/api_server.py`` via ``register_options_lab_routes(app)``.

- ``GET /api/options-lab/surface?ticker=SPY`` — multi-expiry implied-volatility
  surface (per-strike IV curves plus ATM IV / OTM-skew summaries), feeding the
  vol-surface chart.
- ``GET /api/options-lab/chain?ticker=SPY&expiration=...`` — one expiration's
  full calls/puts ladder enriched with Black-Scholes Greeks priced at each
  contract's own implied volatility.

Both are read-only views over the shared Yahoo client (throttled per host).
The heavy lifting lives in :mod:`backtest.options_analytics` so it can be
tested without a server; these endpoints only adapt it to HTTP.
"""

from __future__ import annotations

import sys as _sys
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

__all__ = [
    "OptionsSurfaceResponse",
    "OptionsChainResponse",
    "register_options_lab_routes",
]


# ============================================================================
# Pydantic Models
# ============================================================================


class OptionsContractPoint(BaseModel):
    """One strike on the vol surface for a single expiration."""

    strike: float = Field(..., description="Strike price")
    moneyness: Optional[float] = Field(None, description="strike / spot ratio")
    iv_call: Optional[float] = Field(None, description="Call implied volatility")
    iv_put: Optional[float] = Field(None, description="Put implied volatility")
    delta_call: Optional[float] = Field(None, description="BSM call delta at its own IV")
    delta_put: Optional[float] = Field(None, description="BSM put delta at its own IV")


class OptionsSurfaceExpiry(BaseModel):
    """One expiration leg of the vol surface."""

    expiration: int = Field(..., description="Expiration as epoch seconds")
    days_to_expiry: float = Field(..., description="Calendar days until expiry")
    atm_iv: Optional[float] = Field(None, description="Average IV of the strike nearest spot")
    skew: Optional[float] = Field(None, description="OTM put IV minus OTM call IV near 25-delta")
    contracts: list[OptionsContractPoint] = Field(default_factory=list)


class OptionsSurfaceResponse(BaseModel):
    """The full vol surface payload."""

    ticker: str = Field(..., description="Requested underlying symbol")
    spot: Optional[float] = Field(None, description="Latest underlying price")
    as_of: str = Field(..., description="UTC ISO instant of the snapshot")
    risk_free_rate: float = Field(..., description="Annualised rate used for delta math")
    expirations: list[OptionsSurfaceExpiry] = Field(default_factory=list)


class OptionsChainContract(BaseModel):
    """One contract row of the Greeks ladder."""

    type: str = Field(..., description="'call' or 'put'")
    strike: float = Field(..., description="Strike price")
    iv: Optional[float] = Field(None, description="Implied volatility")
    bid: Optional[float] = Field(None, description="Best bid")
    ask: Optional[float] = Field(None, description="Best ask")
    last: Optional[float] = Field(None, description="Last traded price")
    open_interest: Optional[int] = Field(None, description="Open interest (contracts)")
    volume: Optional[int] = Field(None, description="Session volume (contracts)")
    delta: Optional[float] = Field(None, description="BSM delta at the contract's IV")
    gamma: Optional[float] = Field(None, description="BSM gamma at the contract's IV")
    theta: Optional[float] = Field(None, description="BSM theta per day at the contract's IV")
    vega: Optional[float] = Field(None, description="BSM vega per 1% vol at the contract's IV")


class OptionsChainResponse(BaseModel):
    """The Greeks-ladder payload for one expiration."""

    ticker: str = Field(..., description="Requested underlying symbol")
    spot: Optional[float] = Field(None, description="Latest underlying price")
    expiration: Optional[int] = Field(None, description="Expiration as epoch seconds")
    days_to_expiry: Optional[float] = Field(None, description="Calendar days until expiry")
    contracts: list[OptionsChainContract] = Field(default_factory=list)


# ============================================================================
# Route registration
# ============================================================================


def register_options_lab_routes(
    app: FastAPI,
    require_auth: Any | None = None,
) -> None:
    """Mount the Options Lab endpoints onto ``app``.

    Args:
        app: The FastAPI application to register routes on.
        require_auth: Auth dependency; when ``None`` it is resolved from the
            ``api_server`` module (mirroring ``register_autopilot_routes``).
    """
    h = _sys.modules.get("api_server")
    if h is None:
        raise RuntimeError(
            "register_options_lab_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )
    if require_auth is None:
        require_auth = h.require_auth

    @app.get(
        "/api/options-lab/surface",
        response_model=OptionsSurfaceResponse,
        dependencies=[Depends(require_auth)],
    )
    async def options_surface_endpoint(
        ticker: str = Query(..., min_length=1, description="US underlying symbol, e.g. SPY"),
        max_expirations: int = Query(4, ge=1, le=6, description="Expirations to include"),
    ) -> OptionsSurfaceResponse:
        """Return the multi-expiry implied-volatility surface."""
        from backtest.options_analytics import build_vol_surface

        try:
            surface = build_vol_surface(ticker, max_expirations=max_expirations)
        except Exception as exc:  # noqa: BLE001 - upstream failures surface as 502
            raise HTTPException(status_code=502, detail=f"options surface fetch failed: {exc}") from exc
        return OptionsSurfaceResponse(**surface)

    @app.get(
        "/api/options-lab/chain",
        response_model=OptionsChainResponse,
        dependencies=[Depends(require_auth)],
    )
    async def options_chain_endpoint(
        ticker: str = Query(..., min_length=1, description="US underlying symbol, e.g. SPY"),
        expiration: Optional[int] = Query(None, description="Expiration as epoch seconds; omit for nearest"),
    ) -> OptionsChainResponse:
        """Return the Greeks ladder for one expiration."""
        from backtest.options_analytics import build_chain_greeks

        try:
            chain = build_chain_greeks(ticker, expiration=expiration)
        except Exception as exc:  # noqa: BLE001 - upstream failures surface as 502
            raise HTTPException(status_code=502, detail=f"options chain fetch failed: {exc}") from exc
        return OptionsChainResponse(**chain)
