"""Crypto mined volume factor: price-range volume absorption rank."""
from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_density_rank_absorption",
    "nickname": "VolumeDensityRank",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{zscore}\left(DWMA_5\left(\operatorname{ts\_rank}\left(\frac{V}{(H-L)/C},20\right)\right)\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 35,
    "notes": "Measures recent volume per unit of relative intraday range as a rolling within-asset percentile, then applies cross-sectional z-score.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    relative_range = safe_div(high - low, close)
    volume_density = safe_div(volume, relative_range)
    density_rank = ts_rank(volume_density, 20)
    smoothed_density_rank = decay_linear(density_rank, 5)

    return zscore(smoothed_density_rank)