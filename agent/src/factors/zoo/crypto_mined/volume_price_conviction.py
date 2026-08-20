"""Crypto mined volume-price conviction factor.

Volume-confirmed momentum: rolling correlation between daily price changes
and volume, scaled by normalized 10-bar momentum.
"""

from __future__ import annotations

import pandas as pd

from src.factors.base import (
    decay_linear,
    delta,
    rank,
    safe_div,
    ts_corr,
    ts_std,
)

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_conviction",
    "nickname": "VolumePriceConviction",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\mathrm{decay}_{5}\\left(\\rho_{20}(\\Delta C, V) \\cdot \\frac{\\Delta_{10} C}{\\sigma_{20}(C)}\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Confirms momentum when volume moves with price changes; cross-sectionally ranked after decay.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-confirmed momentum signal aligned to close index."""
    close = panel["close"]
    volume = panel["volume"]

    ret = delta(close, 1)
    corr = ts_corr(ret, volume, 20)
    momentum = safe_div(delta(close, 10), ts_std(close, 20))
    raw = corr * momentum

    return rank(decay_linear(raw, 5))