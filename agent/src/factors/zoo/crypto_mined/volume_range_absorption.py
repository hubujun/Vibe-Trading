"""Volume-range absorption: volume absorbed per unit high-low range."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_absorption",
    "nickname": "VolumeRangeAbsorption",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\mathrm{ts\\_rank}_{20}\\left(\\frac{V_t}{H_t-L_t}\\right)\\right)",
    "columns_required": ["close", "volume", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "Ranks the 20-bar within-asset percentile of volume absorbed per unit price range. High values indicate high volume relative to range expansion.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    high = panel["high"]
    low = panel["low"]
    volume = panel["volume"]

    range_ = high - low
    absorption = safe_div(volume, range_)
    absorption_rank = ts_rank(absorption, 20)

    return rank(absorption_rank).reindex(index=close.index, columns=close.columns)