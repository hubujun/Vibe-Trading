"""crypto_mined_volume_close_pressure: volume-weighted candle close location."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_close_pressure",
    "nickname": "vol_close_pressure",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\frac{C_t-L_t}{H_t-L_t} \\times \\mathrm{ts\\_rank}_{20}(V_t)\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "Cross-sectional rank of daily close location weighted by 20-bar volume percentile.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    close_location = safe_div(close - low, high - low)
    volume_percentile = ts_rank(volume, 20)

    return rank(close_location * volume_percentile)