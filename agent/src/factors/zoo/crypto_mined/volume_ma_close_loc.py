"""Crypto mined volume convergence close-location factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_ma_close_loc",
    "nickname": "Volume Close Loc",
    "theme": ["volume"],
    "formula_latex": "\\operatorname{rank}_t\\left(\\frac{MA_5(V_t)}{MA_{20}(V_t)} \\cdot \\frac{C_t}{H_t}\\right)",
    "columns_required": ["close", "high", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "Combines short-term volume expansion relative to a 20-day volume average with today's close location inside the daily high. It targets accumulation-like volume while price closes near the high.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return cross-sectional rank of volume expansion conditioned on close location."""
    close = panel["close"]
    high = panel["high"]
    volume = panel["volume"]

    short_volume = ts_mean(volume, 5)
    long_volume = ts_mean(volume, 20)
    volume_pressure = safe_div(short_volume, long_volume)

    close_location = safe_div(close, high)

    return rank(volume_pressure * close_location)