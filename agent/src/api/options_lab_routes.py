"""Options Lab HTTP routes — vol surface + Greeks dashboard backend.

Mounted by ``agent/api_server.py`` via ``register_options_lab_routes(app)``.

- ``GET /api/options-lab/surface?ticker=SPY`` — multi-expiry implied-volatility
  surface (per-strike IV curves plus ATM IV / OTM-skew summaries), feeding the
  vol-surface chart.
- ``GET /api/options-lab/chain?ticker=SPY&expiration=...`` — one expiration's
  full calls/puts ladder enriched with Black-Scholes Greeks priced at each
  contract's own implied volatility.
- ``GET /api/options-lab/payoff?strategy=...&lower_strike=...`` — analytic
  expiry payoff curve and risk summary for parameterized multi-leg strategies
  (bull call spread / long straddle / iron condor). Pure local math — no data
  fetch.

Both market-data views are read-only over the shared Yahoo client (throttled
per host). The heavy lifting lives in :mod:`backtest.options_analytics` and
:mod:`backtest.options_payoff` so it can be tested without a server; these
endpoints only adapt it to HTTP.
"""

from __future__ import annotations

import sys as _sys
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

__all__ = [
    "OptionsSurfaceResponse",
    "OptionsChainResponse",
    "OptionsPayoffResponse",
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


class PayoffPoint(BaseModel):
    """One point of the expiry payoff curve."""

    spot: float = Field(..., description="Underlying spot at expiry")
    pnl: float = Field(..., description="Strategy P&L at this spot (per multiplier unit)")


class OptionsPayoffResponse(BaseModel):
    """Analytic expiry payoff curve plus strategy risk summary.

    ``max_profit`` / ``max_loss`` are ``null`` when the corresponding tail is
    unbounded (JSON cannot carry infinities); the companion ``*_unbounded``
    booleans disambiguate.
    """

    strategy: str = Field(..., description="Strategy name as requested")
    entry_spot: float = Field(..., description="Underlying spot at entry")
    time_to_expiry: float = Field(..., description="Years until expiry at entry")
    rate: float = Field(..., description="Annual continuously compounded risk-free rate")
    iv: float = Field(..., description="Annualized volatility used for entry pricing")
    multiplier: float = Field(..., description="Currency multiplier per option price unit")
    net_premium: float = Field(..., description="Signed gross entry premium (debit positive)")
    entry_commission: float = Field(..., description="Entry commission paid")
    entry_cost: float = Field(..., description="Gross premium plus entry commission")
    breakevens: list[float] = Field(default_factory=list, description="Isolated zero-P&L expiry spots")
    max_profit: Optional[float] = Field(None, description="Analytic max profit; null when unbounded")
    max_loss: Optional[float] = Field(None, description="Analytic max loss; null when unbounded")
    profit_unbounded: bool = Field(..., description="Right-tail profit is unbounded")
    loss_unbounded: bool = Field(..., description="Right-tail loss is unbounded")
    curve: list[PayoffPoint] = Field(default_factory=list, description="Expiry payoff curve points")


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

    @app.get(
        "/api/options-lab/payoff",
        response_model=OptionsPayoffResponse,
        dependencies=[Depends(require_auth)],
    )
    async def options_payoff_endpoint(
        strategy: str = Query(
            ...,
            description="Strategy template: bull_call_spread | long_straddle | iron_condor",
        ),
        lower_strike: Optional[float] = Query(None, gt=0, description="bull_call_spread lower strike"),
        upper_strike: Optional[float] = Query(None, gt=0, description="bull_call_spread upper strike"),
        strike: Optional[float] = Query(None, gt=0, description="long_straddle strike"),
        put_wing: Optional[float] = Query(None, gt=0, description="iron_condor put wing"),
        put_body: Optional[float] = Query(None, gt=0, description="iron_condor put body"),
        call_body: Optional[float] = Query(None, gt=0, description="iron_condor call body"),
        call_wing: Optional[float] = Query(None, gt=0, description="iron_condor call wing"),
        qty: int = Query(1, ge=1, le=100, description="Contract quantity per leg"),
        entry_spot: Optional[float] = Query(None, gt=0, description="Entry spot; defaults to mean strike"),
        time_to_expiry: float = Query(0.25, gt=0, description="Years until expiry at entry"),
        rate: float = Query(0.05, description="Risk-free rate"),
        iv: float = Query(0.3, gt=0, description="Entry IV for legs without explicit premium"),
        multiplier: float = Query(1.0, gt=0, description="Currency per option price unit"),
        commission_rate: float = Query(0.001, ge=0, description="Entry commission as premium fraction"),
        points: int = Query(201, ge=2, le=2001, description="Payoff curve grid points"),
    ) -> OptionsPayoffResponse:
        """Return an analytic expiry payoff curve for a parameterized strategy.

        Pure local math (no market data fetch): entry premiums are priced with
        Black-Scholes at the supplied entry spot / IV, then the expiry payoff is
        solved from the piecewise-linear structure.
        """
        from backtest.options_payoff import (
            bull_call_spread,
            default_spot_grid,
            expiry_payoff,
            iron_condor,
            long_straddle,
        )

        try:
            if strategy == "bull_call_spread":
                if lower_strike is None or upper_strike is None:
                    raise ValueError("bull_call_spread requires lower_strike and upper_strike")
                legs = bull_call_spread(lower_strike, upper_strike, qty)
            elif strategy == "long_straddle":
                if strike is None:
                    raise ValueError("long_straddle requires strike")
                legs = long_straddle(strike, qty)
            elif strategy == "iron_condor":
                if any(v is None for v in (put_wing, put_body, call_body, call_wing)):
                    raise ValueError("iron_condor requires put_wing, put_body, call_body, call_wing")
                legs = iron_condor(put_wing, put_body, call_body, call_wing, qty)
            else:
                raise ValueError(
                    f"unknown strategy {strategy!r}; "
                    "expected bull_call_spread | long_straddle | iron_condor"
                )

            entry = entry_spot if entry_spot is not None else float(
                sum(leg.strike for leg in legs) / len(legs)
            )
            grid = default_spot_grid(entry, half_width_pct=0.5, points=points)
            report = expiry_payoff(
                legs,
                grid,
                entry_spot=entry,
                time_to_expiry=time_to_expiry,
                rate=rate,
                iv=iv,
                multiplier=multiplier,
                commission_rate=commission_rate,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - unexpected math failures surface as 502
            raise HTTPException(status_code=502, detail=f"options payoff failed: {exc}") from exc

        return OptionsPayoffResponse(
            strategy=strategy,
            entry_spot=entry,
            time_to_expiry=time_to_expiry,
            rate=rate,
            iv=iv,
            multiplier=multiplier,
            net_premium=round(float(report.net_premium), 4),
            entry_commission=round(float(report.entry_commission), 4),
            entry_cost=round(float(report.entry_cost), 4),
            breakevens=[round(float(b), 4) for b in report.breakevens],
            max_profit=None if report.profit_unbounded else round(float(report.max_profit), 4),
            max_loss=None if report.loss_unbounded else round(float(report.max_loss), 4),
            profit_unbounded=report.profit_unbounded,
            loss_unbounded=report.loss_unbounded,
            curve=[
                PayoffPoint(spot=float(s), pnl=float(p))
                for s, p in zip(report.spot_grid, report.payoff, strict=False)
            ],
        )
