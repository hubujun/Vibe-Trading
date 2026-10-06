"""crypto VOLUME/MOMENTUM: volume-weighted close position within the high-low range."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_range_position",
    "nickname": "量价区间位置",
    "theme": ["volume", "momentum"],
    "formula_latex": "\\frac{\\sum_{i=0}^{n-1} \\mathrm{vol}_{t-i} \\cdot \\mathrm{pos}_{t-i}}{\\sum_{i=0}^{n-1} \\mathrm{vol}_{t-i}} - \\frac{1}{n}\\sum_{i=0}^{n-1} \\mathrm{pos}_{t-i}",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 14,
    "min_warmup_bars": 14,
    "notes": "Measures whether volume has been concentrated near the high of the daily range, relative to the simple average close position.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-weighted range-position minus the simple range-position."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    pos = safe_div(close - low, high - low)
    n = 14
    vw_pos = safe_div(ts_mean(volume * pos, n), ts_mean(volume, n))
    simple_pos = ts_mean(pos, n)
    return vw_pos - simple_pos