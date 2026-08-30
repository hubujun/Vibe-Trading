"""crypto MINED VOLUME: volume-weighted close location rank."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_location",
    "nickname": "volume_weighted_close_location",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\frac{\\mathrm{MA}_{20}\\left[V_t(2C_t-H_t-L_t)\\right]}{\\mathrm{MA}_{20}(V_t)}\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 20,
    "notes": "Volume-weighted average close location; positive values show heavy volume is associated with closes near the high.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float).reindex_like(close)
    low = panel["low"].astype(float).reindex_like(close)
    volume = panel["volume"].astype(float).reindex_like(close)

    location = 2.0 * close - high - low
    weighted_location = location * volume

    numerator = ts_mean(weighted_location, 20)
    denominator = ts_mean(volume, 20)
    avg_location = safe_div(numerator, denominator)

    return rank(avg_location)