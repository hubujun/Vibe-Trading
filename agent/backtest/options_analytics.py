"""Options Lab analytics — implied-volatility surface and chain Greeks.

Pure computation layered over the shared Yahoo client: it pulls one spot
quote and one or more option-chain blocks (throttled per host by
``backtest.loaders.yahoo_client``) and turns them into dashboard-shaped data:

- :func:`build_vol_surface` — per-expiry IV curves (strike x moneyness) with
  ATM IV and OTM-skew summaries, for the vol-surface chart.
- :func:`build_chain_greeks` — one expiry's full calls/puts ladder enriched
  with Black-Scholes Greeks (delta/gamma/theta/vega) priced at each
  contract's own implied volatility.

Nothing here performs HTTP itself: the Yahoo functions are injected through
the module namespace so tests can patch them directly.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from backtest.engines.options_portfolio import bs_greeks
from backtest.loaders import yahoo_client

#: Maximum expirations pulled for a vol surface (one throttled request each).
_MAX_SURFACE_EXPIRATIONS = 6

#: Delta window used to locate the 25-delta OTM contracts for skew.
_SKEW_DELTA_TARGET = 0.25
_SKEW_DELTA_WINDOW = 0.05

#: Fallback risk-free rate when the caller does not supply one.
_DEFAULT_RISK_FREE_RATE = 0.05


def fetch_spot(ticker: str) -> Optional[float]:
    """Return the latest regular-market price for ``ticker``.

    Uses the ``price`` quoteSummary module; returns ``None`` when the quote
    is missing or non-finite (e.g. a delisted or unknown symbol).
    """
    try:
        summary = yahoo_client.get_quote_summary(ticker, modules=["price"])
    except Exception:  # noqa: BLE001 - upstream failure degrades to None
        return None
    price = (summary or {}).get("price") or {}
    value = price.get("regularMarketPrice")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _days_to_expiry(expiration: int, now: datetime) -> float:
    """Calendar days between the reference instant and the expiry date."""
    expiry_dt = datetime.fromtimestamp(int(expiration), tz=timezone.utc)
    return (expiry_dt - now).total_seconds() / 86400.0


def _contract_map(contracts: Sequence[Dict[str, Any]]) -> Dict[float, Dict[str, Any]]:
    """Index one calls/puts array by strike (larger duplicate wins)."""
    by_strike: Dict[float, Dict[str, Any]] = {}
    for contract in contracts or []:
        strike = contract.get("strike")
        if strike is None:
            continue
        key = float(strike)
        if key not in by_strike or _contract_premium(contract) > _contract_premium(
            by_strike[key]
        ):
            by_strike[key] = contract
    return by_strike


def _contract_premium(contract: Dict[str, Any]) -> float:
    """Best available premium proxy (last, else bid/ask midpoint, else 0)."""
    last = contract.get("lastPrice")
    if last is not None:
        try:
            return float(last)
        except (TypeError, ValueError):
            pass
    bid, ask = contract.get("bid"), contract.get("ask")
    if bid is not None and ask is not None:
        try:
            return (float(bid) + float(ask)) / 2.0
        except (TypeError, ValueError):
            pass
    return 0.0


def _to_float(value: Any) -> Optional[float]:
    """Coerce a numeric contract field to float, or ``None`` when absent/bad."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _iv_for(contract: Dict[str, Any]) -> Optional[float]:
    """Extract a sane implied volatility (clamped to (0, 5])."""
    iv = _to_float(contract.get("impliedVolatility"))
    if iv is None or iv <= 0.0 or iv > 5.0:
        return None
    return iv


def summarize_surface_rows(
    rows: Sequence[Dict[str, Any]], spot: float
) -> Dict[str, Any]:
    """Derive ATM IV and OTM-skew summaries from per-strike surface rows.

    Args:
        rows: Rows with ``strike``, ``iv_call``, ``iv_put``, ``delta_call``,
            ``delta_put`` (any may be ``None``).
        spot: Current underlying price.

    Returns:
        ``{"atm_iv": float|None, "skew": float|None}`` where ``atm_iv`` is the
        average IV of the strike closest to spot and ``skew`` is OTM-put IV
        minus OTM-call IV near the 25-delta (positive = puts price in a
        crash, i.e. the usual equity negative skew).
    """
    atm_iv: Optional[float] = None
    best_distance: Optional[float] = None
    for row in rows:
        iv_call, iv_put = row.get("iv_call"), row.get("iv_put")
        if iv_call is None and iv_put is None:
            continue
        distance = abs(float(row["strike"]) - spot)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            values = [v for v in (iv_call, iv_put) if v is not None]
            atm_iv = sum(values) / len(values) if values else None

    put_iv = _avg_iv_near_delta(rows, side="put", target=_SKEW_DELTA_TARGET)
    call_iv = _avg_iv_near_delta(rows, side="call", target=_SKEW_DELTA_TARGET)
    skew = put_iv - call_iv if put_iv is not None and call_iv is not None else None
    return {"atm_iv": atm_iv, "skew": skew}


def _avg_iv_near_delta(
    rows: Sequence[Dict[str, Any]], *, side: str, target: float
) -> Optional[float]:
    """Average IV of contracts whose |delta| lands in the target window."""
    collected: List[float] = []
    for row in rows:
        delta = row.get(f"delta_{side}")
        iv = row.get(f"iv_{side}")
        if delta is None or iv is None:
            continue
        if abs(abs(delta) - target) <= _SKEW_DELTA_WINDOW:
            collected.append(iv)
    if not collected:
        return None
    return sum(collected) / len(collected)


def build_vol_surface(
    ticker: str,
    *,
    max_expirations: int = 4,
    risk_free_rate: float = _DEFAULT_RISK_FREE_RATE,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build a multi-expiry implied-volatility surface for ``ticker``.

    Args:
        ticker: US underlying symbol.
        max_expirations: Number of expirations to fetch (clamped to
            :data:`_MAX_SURFACE_EXPIRATIONS`; each costs one throttled
            request). The nearest ones are used.
        risk_free_rate: Annualised rate used only for delta (skew) math.
        now: Reference instant for days-to-expiry (defaults to UTC now).

    Returns:
        A surface payload: ``ticker``, ``spot``, ``as_of``, ``risk_free_rate``
        and ``expirations``, each with ``expiration``, ``days_to_expiry``,
        ``atm_iv``, ``skew`` and ``contracts`` (strike/moneyness/iv/delta).
        When no chain can be fetched, ``expirations`` is empty.
    """
    now = now or datetime.now(timezone.utc)
    chain = yahoo_client.get_options(ticker)
    expirations = [int(e) for e in (chain.get("expirationDates") or []) if e is not None]
    expirations = expirations[: max(1, min(int(max_expirations), _MAX_SURFACE_EXPIRATIONS))]

    spot = fetch_spot(ticker)
    blocks: List[Dict[str, Any]] = []
    for expiration in expirations:
        block = yahoo_client.get_options(ticker, expiration=expiration)
        options = block.get("options") or []
        if not options:
            continue
        blocks.append(_surface_block(options[0], spot, expiration, now, risk_free_rate))
    return {
        "ticker": ticker,
        "spot": spot,
        "as_of": now.isoformat(),
        "risk_free_rate": risk_free_rate,
        "expirations": blocks,
    }


def _surface_block(
    block: Dict[str, Any],
    spot: Optional[float],
    expiration: int,
    now: datetime,
    risk_free_rate: float,
) -> Dict[str, Any]:
    """Fold one Yahoo options block into per-strike surface rows."""
    calls = _contract_map(block.get("calls"))
    puts = _contract_map(block.get("puts"))
    strikes = sorted(set(calls) | set(puts))
    T = max(_days_to_expiry(expiration, now), 0.0) / 365.0

    rows: List[Dict[str, Any]] = []
    for strike in strikes:
        call, put = calls.get(strike), puts.get(strike)
        iv_call = _iv_for(call) if call else None
        iv_put = _iv_for(put) if put else None
        delta_call = _contract_delta(call, spot, strike, T, risk_free_rate, iv_call)
        delta_put = _contract_delta(put, spot, strike, T, risk_free_rate, iv_put)
        rows.append(
            {
                "strike": strike,
                "moneyness": round(strike / spot, 4) if spot else None,
                "iv_call": iv_call,
                "iv_put": iv_put,
                "delta_call": delta_call,
                "delta_put": delta_put,
            }
        )

    summary = summarize_surface_rows(rows, spot) if spot else {"atm_iv": None, "skew": None}
    return {
        "expiration": int(expiration),
        "days_to_expiry": round(max(_days_to_expiry(expiration, now), 0.0), 3),
        "atm_iv": summary["atm_iv"],
        "skew": summary["skew"],
        "contracts": rows,
    }


def _contract_delta(
    contract: Optional[Dict[str, Any]],
    spot: Optional[float],
    strike: float,
    T: float,
    risk_free_rate: float,
    iv: Optional[float],
) -> Optional[float]:
    """Black-Scholes delta for one contract, or ``None`` when unpriced."""
    if contract is None or spot is None or iv is None:
        return None
    option_type = "call" if "C" in str(contract.get("contractSymbol", "")).upper() else "put"
    try:
        greeks = bs_greeks(
            S=float(spot),
            K=float(strike),
            T=T,
            r=risk_free_rate,
            sigma=iv,
            option_type=option_type,
        )
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return round(float(greeks["delta"]), 4)


def build_chain_greeks(
    ticker: str,
    expiration: Optional[int] = None,
    *,
    risk_free_rate: float = _DEFAULT_RISK_FREE_RATE,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the full calls/puts Greeks ladder for one expiration.

    Args:
        ticker: US underlying symbol.
        expiration: Epoch seconds; omit for the nearest expiration.
        risk_free_rate: Annualised rate for Black-Scholes Greeks.
        now: Reference instant for days-to-expiry (defaults to UTC now).

    Returns:
        A chain payload: ``ticker``, ``spot``, ``expiration``,
        ``days_to_expiry`` and ``contracts`` — each contract carrying type,
        strike, IV, bid/ask/last, open interest, volume and the four Greeks
        priced at its own IV. Unpriced contracts keep ``iv``/Greeks ``None``.
    """
    now = now or datetime.now(timezone.utc)
    chain = yahoo_client.get_options(ticker, expiration=expiration)
    options = chain.get("options") or []
    if not options:
        return {"ticker": ticker, "spot": None, "expiration": None, "days_to_expiry": None, "contracts": []}

    block = options[0]
    actual_expiration = int(block.get("expirationDate") or 0)
    days = max(_days_to_expiry(actual_expiration, now), 0.0) if actual_expiration else None
    T = days / 365.0 if days is not None else 0.0
    spot = fetch_spot(ticker)

    contracts: List[Dict[str, Any]] = []
    for side, option_type in (("calls", "call"), ("puts", "put")):
        for contract in block.get(side) or []:
            strike = _to_float(contract.get("strike"))
            if strike is None:
                continue
            iv = _iv_for(contract)
            greeks = None
            if spot is not None and iv is not None:
                try:
                    raw = bs_greeks(
                        S=spot, K=strike, T=T, r=risk_free_rate,
                        sigma=iv, option_type=option_type,
                    )
                    greeks = {key: round(float(value), 6) for key, value in raw.items()}
                except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                    greeks = None
            contracts.append(
                {
                    "type": option_type,
                    "strike": strike,
                    "iv": iv,
                    "bid": _to_float(contract.get("bid")),
                    "ask": _to_float(contract.get("ask")),
                    "last": _to_float(contract.get("lastPrice")),
                    "open_interest": contract.get("openInterest"),
                    "volume": contract.get("volume"),
                    "delta": greeks["delta"] if greeks else None,
                    "gamma": greeks["gamma"] if greeks else None,
                    "theta": greeks["theta"] if greeks else None,
                    "vega": greeks["vega"] if greeks else None,
                }
            )
    contracts.sort(key=lambda row: (row["strike"], row["type"]))
    return {
        "ticker": ticker,
        "spot": spot,
        "expiration": actual_expiration or None,
        "days_to_expiry": round(days, 3) if days is not None else None,
        "contracts": contracts,
    }
