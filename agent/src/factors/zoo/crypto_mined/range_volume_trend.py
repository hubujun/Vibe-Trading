"""crypto MOMENTUM: Donchian range position with volume trend confirmation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_max, ts_mean, ts_min, zscore

__alpha_meta__ = {
    "id": "crypto_mined_range_volume_trend",
    "nickname": "区间量能趋势",
    "theme": ["momentum", "volume"],
    "formula_latex": r"\mathrm{zscore}\left( \left( \frac{close - \min(low,30)}{\max(high,30) - \min(low,30)} - 0.5 \right) \times \frac{\mathrm{MA}_{10}(volume)}{\mathrm{MA}_{30}(volume)} \right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 30,
    "notes": "Price position in 30-day Donchian range times short/long volume moving average ratio.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the range position with volume trend confirmation factor."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    upper = ts_max(high, 30)
    lower = ts_min(low, 30)
    range_pos = safe_div(close - lower, upper - lower) - 0.5
    vol_ratio = safe_div(ts_mean(volume, 10), ts_mean(volume, 30))

    raw = range_pos * vol_ratio
    return zscore(raw)