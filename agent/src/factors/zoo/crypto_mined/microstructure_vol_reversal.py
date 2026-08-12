"""crypto microstructure REVERSAL: volume-scaled close location in range."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_microstructure_vol_reversal",
    "nickname": "量能位置反转",
    "theme": ["reversal"],
    "formula_latex": r"-\operatorname{rank}_t(\operatorname{ts\_rank}_{20}(-\operatorname{decay\_linear}_5((C-L)/(H-L)\cdot V/\operatorname{mean}(V,20))))",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 45,
    "notes": "Close location within the high-low range scaled by volume surge; high-volume closes near highs are contrarian reversal signals.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    intraday_pos = safe_div(close - low, high - low)
    vol_surge = safe_div(volume, ts_mean(volume, 20))
    buying_pressure = intraday_pos * vol_surge
    reversal_score = -decay_linear(buying_pressure, 5)

    return rank(ts_rank(reversal_score, 20))