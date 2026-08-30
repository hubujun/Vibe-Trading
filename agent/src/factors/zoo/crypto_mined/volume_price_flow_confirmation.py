"""Crypto mined factor: volume-price flow confirmation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_flow_confirmation",
    "nickname": "VolumeFlowConf",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{zscore}\\left(\\rho\\left(\\frac{\\Delta C}{C_{-1}}, \\frac{\\Delta V}{V_{-1}}, 20\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 22,
    "notes": "Cross-sectional z-score of rolling correlation between close returns and volume growth.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-price flow confirmation factor aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    d_close = delta(close, 1)
    d_volume = delta(volume, 1)

    close_ret = safe_div(d_close, close - d_close)
    volume_ret = safe_div(d_volume, volume - d_volume)

    corr = ts_corr(close_ret, volume_ret, 20)
    return zscore(corr)