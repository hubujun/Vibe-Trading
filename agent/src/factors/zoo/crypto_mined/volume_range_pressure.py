"""crypto mined VOLUME: range-scaled volume pressure anomaly."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_pressure",
    "nickname": "RangeVolumePressure",
    "theme": ["volume"],
    "formula_latex": r"F_t = \operatorname{zscore}\left(\operatorname{decay\_linear}_{10}\left(\operatorname{ts\_rank}_{20}\left(\frac{V_t}{(H_t-L_t)/C_t}\right)\right)\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 35,
    "notes": "Measures abnormal volume per unit of fractional high-low range. Uses per-symbol rolling rank, smoothing, and a final cross-sectional z-score.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    fraction_range = safe_div(high - low, close)
    volume_per_range = safe_div(volume, fraction_range)
    range_volume_rank = ts_rank(volume_per_range, 20)
    smoothed_rank = decay_linear(range_volume_rank, 10)
    return zscore(smoothed_rank)