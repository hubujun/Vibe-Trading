"""Volume-confirmed wide-range mean reversion for crypto."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_reversal",
    "nickname": "VolRangeReversal",
    "theme": ["volume"],
    "formula_latex": r"-\operatorname{zscore}\left(\operatorname{decay}\left(\operatorname{ts\_rank}(V,20)\cdot\operatorname{ts\_rank}((H-L)/C,20),3\right)\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 30,
    "notes": "Reacts to bars with above-median volume and wide price range; negative z-score implies short-term reversal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_pct = safe_div(high - low, close)
    vol_rank = ts_rank(volume, 20)
    range_rank = ts_rank(range_pct, 20)
    signal = decay_linear(vol_rank * range_rank, 3)

    return -zscore(signal)