"""crypto MICROSTRUCTURE: volume-weighted close position within daily range."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_vol_weighted_range_pos",
    "nickname": "量价加权区间位置",
    "theme": ["microstructure", "volume"],
    "formula_latex": "\\mathrm{zscore}\\!\\left(\\frac{\\sum_{i=0}^{19} V_{t-i}\\cdot\\frac{2C_{t-i}-H_{t-i}-L_{t-i}}{H_{t-i}-L_{t-i}}}{\\sum_{i=0}^{19} V_{t-i}}\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "Persistent intraday buying pressure: aggregating close position in the daily range using volume as weights, then cross-sectional z-score.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Volume-weighted average of close position within the daily range."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    rng = high - low
    pos = safe_div(2.0 * close - high - low, rng)
    weighted = pos * volume
    num = ts_mean(weighted, 20)
    den = ts_mean(volume, 20)
    avg_pos = safe_div(num, den)
    return zscore(avg_pos)