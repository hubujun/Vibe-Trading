"""Crypto microstructure: close location in the daily range, volume-confirmed reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_microstructure_close_reversal",
    "nickname": "CloseLocationFlow",
    "theme": ["microstructure"],
    "formula_latex": "zscore\\left(-zscore\\left(\\frac{C-L}{H-L}-0.5\\right) \\cdot \\mathrm{ts\\_rank}(V,20)\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "Fades closing prices near daily range extremes when volume is high.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_size = high - low
    close_loc = safe_div(close - low, range_size)
    loc_dev = close_loc - 0.5
    volume_rank = ts_rank(volume, 20)

    raw = -zscore(loc_dev) * volume_rank
    return zscore(raw)