"""Crypto mined volume factor: smoothed volume-return correlation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, safe_div, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr_flow",
    "nickname": "VolRetCorrFlow",
    "theme": ["volume"],
    "formula_latex": "zscore\\left(\\mathrm{decay\\_linear}\\left(\\mathrm{Corr}_{20}\\left(\\frac{P_t-P_{t-1}}{P_{t-1}}, \\Delta V_t\\right),5\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Decayed rolling correlation between daily returns and daily volume changes, cross-sectionally z-scored. Identifies assets where volume flow is aligned with price pressure.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    vol_chg = delta(volume, 1)
    corr = ts_corr(ret, vol_chg, 20)

    return zscore(decay_linear(corr, 5))