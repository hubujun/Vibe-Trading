"""crypto_mined_volume_weighted_close_loc factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_close_loc",
    "nickname": "VolumeWeightedCloseLocation",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\frac{\\sum_i V_i \\cdot \\mathrm{CL}_i}{\\sum_i V_i}\\right), \\quad \\mathrm{CL}_i = \\frac{C_i - L_i}{H_i - L_i}",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "Average volume-weighted close location inside the daily range; high values indicate accumulation near highs during heavy volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-weighted close-location factor aligned to close index."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    close_loc = safe_div(close - low, high - low)
    vw_loc = safe_div(ts_mean(volume * close_loc, 20), ts_mean(volume, 20))

    return rank(vw_loc)