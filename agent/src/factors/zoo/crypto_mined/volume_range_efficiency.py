"""crypto VOLUME: volume-adjusted range efficiency."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_efficiency",
    "nickname": "量能区间效率",
    "theme": ["volume"],
    "formula_latex": "-\\frac{\\mathrm{mean}_n(H_t - L_t)}{\\mathrm{mean}_n(V_t)}",
    "columns_required": ["high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 5,
    "notes": "Negative average high-low range per unit of volume; lower volume required for range expansion is treated as stronger absorption.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)
    rng = high - low
    mean_range = ts_mean(rng, 5)
    mean_volume = ts_mean(volume, 5)
    return -safe_div(mean_range, mean_volume)