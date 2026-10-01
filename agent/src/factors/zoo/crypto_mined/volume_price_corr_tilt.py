"""crypto VOLUME: volume-confirmed trend tilt."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_corr_tilt",
    "nickname": "量价共振倾斜",
    "theme": ["volume"],
    "formula_latex": (
        "z\\left(z\\left(\\mathrm{Corr}_{15}"
        "\\left(r_t,\\frac{\\Delta V_t}{\\overline{V}_{20}}\\right)\\right)"
        "\\cdot z\\left(\\frac{C_t}{\\overline{C}_{20}}-1\\right)\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 15,
    "min_warmup_bars": 36,
    "notes": (
        "Tilts the medium-term price trend by the rolling correlation between returns "
        "and relative volume changes. Trends that are accompanied by rising volume "
        "(positive return/volume-change correlation) receive a larger positive score, "
        "while drifts on falling volume are penalised."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-confirmation tilt of the 20-bar price trend."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret_1 = safe_div(delta(close, 1), close.shift(1))
    rel_vol_chg = safe_div(delta(volume, 1), ts_mean(volume, 20))

    confirm = ts_corr(ret_1, rel_vol_chg, 15)
    trend = safe_div(close, ts_mean(close, 20)) - 1.0

    return zscore(zscore(confirm) * zscore(trend))