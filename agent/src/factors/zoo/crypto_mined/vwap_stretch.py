"""crypto VOLUME: stretch versus rolling volume-weighted average price."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_vwap_stretch",
    "nickname": "量权均价偏离",
    "theme": ["volume"],
    "formula_latex": (
        "-z\\left(\\frac{C_t - \\bar{C}^{V}_{20}}{\\bar{C}^{V}_{20}}\\right),"
        "\\quad \\bar{C}^{V}_{20} = \\frac{\\sum_{i<t} V_i C_i}{\\sum_{i<t} V_i}"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 21,
    "notes": (
        "Relative distance of the current close from a 20-bar volume-weighted average "
        "price (turnover centroid). Volume weights mean the centroid only moves when "
        "real flow transacts. The deviation is cross-sectionally z-scored and negated, "
        "so instruments trading well above their volume centroid score low "
        "(stretched / mean-reversion candidate) and those below score high."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Negated cross-sectional z-score of the volume-weighted price stretch."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    turnover = close * volume
    centroid = safe_div(ts_mean(turnover, 20), ts_mean(volume, 20))
    stretch = safe_div(close - centroid, centroid)
    return -zscore(stretch)