"""crypto VOLATILITY: signed semi-deviation tilt of daily returns."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_vol_asym_tilt",
    "nickname": "上行下行波动倾斜",
    "theme": ["volatility"],
    "formula_latex": (
        "-\\mathrm{rank}\\left(\\mathrm{tsmean}_{5}\\left("
        "\\frac{\\sigma^{+}_{20}-\\sigma^{-}_{20}}"
        "{\\sigma^{+}_{20}+\\sigma^{-}_{20}}\\right)\\right)"
    ),
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": (
        "Splits daily log-free simple returns into their positive and negative parts "
        "and takes the 20-bar standard deviation of each semi-series. The normalised "
        "tilt (upside minus downside dispersion, divided by their sum) is smoothed "
        "over 5 bars and ranked, then negated: coins whose upside dispersion dominates "
        "behave like lottery tickets and are expected to underperform those with "
        "downside-dominated dispersion."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Negative cross-sectional rank of the upside/downside semi-deviation tilt."""
    close = panel["close"].astype(float)

    ret = safe_div(close - close.shift(1), close.shift(1))
    up = np.maximum(ret, 0.0)
    dn = np.minimum(ret, 0.0)

    up_vol = ts_std(up, 20)
    dn_vol = ts_std(dn, 20)

    tilt = safe_div(up_vol - dn_vol, up_vol + dn_vol)
    smooth = ts_mean(tilt, 5)
    return -rank(smooth)