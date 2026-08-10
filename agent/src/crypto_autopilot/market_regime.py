"""Market regime classification for the crypto autopilot.

Labels the current market from the local history panel — no network
calls. The equal-weight basket return's lag-1 autocorrelation separates
``trend`` (positive) from ``mean_revert`` (negative) regimes, an
annualised-volatility flag marks ``high_vol`` markets, and the
edge-density machinery from :mod:`backtest.regime` (reused here) adds
the cross-asset ``fused`` correlation context.

The result is descriptive context written into the pipeline state and
the feedback prompt — never a trading signal by itself.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from backtest.regime import compute_edge_density, detect_regimes

logger = logging.getLogger(__name__)

__all__ = ["classify_regime"]

#: Lag-1 autocorrelation above this → trend regime.
_TREND_AUTOCORR: float = 0.05

#: Lag-1 autocorrelation below this → mean-revert regime.
_MEAN_REVERT_AUTOCORR: float = -0.05

#: Annualised volatility (1h bars) above this → high-vol flag.
_HIGH_VOL_ANNUALIZED: float = 1.2

#: Bars per year for annualising 1h-bar volatility.
_BARS_PER_YEAR: int = 365 * 24

#: Minimum bars required before regime statistics are meaningful.
_MIN_BARS: int = 30


def classify_regime(
    close: pd.DataFrame,
    lookback: int = 720,
    corr_window: int = 60,
) -> dict[str, Any]:
    """Classify the trailing market state from a close-price panel.

    Args:
        close: Wide close-price panel (DatetimeIndex x symbols).
        lookback: Trailing bars used for the classification window.
        corr_window: Rolling correlation window (bars) for the fused
            edge-density context.

    Returns:
        Dict with ``regime`` (``"trend"`` | ``"mean_revert"`` |
        ``"mixed"`` | ``"unknown"``), ``high_vol``, ``fused``,
        ``lag1_autocorr``, ``annualized_vol`` and ``bars``.
    """
    empty = {
        "regime": "unknown",
        "high_vol": False,
        "fused": None,
        "lag1_autocorr": None,
        "annualized_vol": None,
        "bars": 0,
    }
    if close is None or close.empty or close.shape[1] < 1:
        return empty

    window = close.iloc[-lookback:]
    rets = window.pct_change(fill_method=None)
    rets = rets.replace([np.inf, -np.inf], np.nan)
    basket = rets.mean(axis=1).dropna()
    if len(basket) < _MIN_BARS:
        return empty

    # Lag-1 autocorrelation of the equal-weight basket return.
    lag1 = float(np.corrcoef(basket.iloc[1:], basket.iloc[:-1])[0, 1])
    if not np.isfinite(lag1):
        lag1 = 0.0
    annualized_vol = float(basket.std(ddof=1) * np.sqrt(_BARS_PER_YEAR))
    high_vol = bool(annualized_vol > _HIGH_VOL_ANNUALIZED)

    if lag1 > _TREND_AUTOCORR:
        regime = "trend"
    elif lag1 < _MEAN_REVERT_AUTOCORR:
        regime = "mean_revert"
    else:
        regime = "mixed"

    # Cross-asset correlation context reusing backtest/regime's state
    # machine. Local-only: feeds the returns frame straight in.
    fused: bool | None = None
    try:
        if rets.shape[1] >= 2 and len(rets) > corr_window:
            density = compute_edge_density(rets, corr_window=corr_window)
            reg = detect_regimes(density)
            last = reg["fused"].dropna().iloc[-1]
            fused = bool(last)
    except Exception as exc:  # noqa: BLE001 — regime context is best-effort
        logger.debug("regime: fused context failed: %s", exc)

    return {
        "regime": regime,
        "high_vol": high_vol,
        "fused": fused,
        "lag1_autocorr": round(lag1, 4),
        "annualized_vol": round(annualized_vol, 4),
        "bars": int(len(window)),
    }
