"""crypto mined volume-range absorption factor."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.factors.base import ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_absorption",
    "nickname": "量能蓄势",
    "theme": ["volume"],
    "formula_latex": "F_t = \\mathrm{zscore}(\\mathrm{ts\\_rank}(\\ln(1+V_t),20) - \\mathrm{ts\\_rank}(H_t-L_t,20))",
    "columns_required": ["high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 20,
    "notes": "Compares each coin's own trailing volume percentile with its high-low range percentile: high volume and narrow range indicate absorption or coiling.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    intraday_range = high - low
    volume_percentile = ts_rank(np.log1p(volume), 20)
    range_percentile = ts_rank(intraday_range, 20)

    return zscore(volume_percentile - range_percentile)