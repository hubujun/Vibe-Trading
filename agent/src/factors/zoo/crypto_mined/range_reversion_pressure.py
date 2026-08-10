"""crypto_mined_range_reversion_pressure: volume-confirmed close-location mean reversion."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, scale, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_range_reversion_pressure",
    "nickname": "RangeReversion",
    "theme": ["reversal"],
    "formula_latex": "-z\\left(\\mathrm{mean}_{10}\\left(\\frac{C-L}{H-L}\\right)\\right) \\cdot \\mathrm{scale}(\\mathrm{ts\\_rank}(V,10))",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 11,
    "notes": "Captures intraday overbought/oversold pressure; high close location with volume weight signals reversal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    close_loc = safe_div(close - low, high - low)
    location_mean = ts_mean(close_loc, 10)
    volume_weight = scale(ts_rank(volume, 10))
    return -zscore(location_mean) * volume_weight