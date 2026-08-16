"""crypto_mined volume-weighted close location."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_close_location",
    "nickname": "VolumeWeightedCloseLocation",
    "theme": ["volume"],
    "formula_latex": "\\frac{\\mathrm{mean}_{20}\\left(V_t \\cdot \\frac{C_t - L_t}{H_t - L_t}\\right)}{\\mathrm{mean}_{20}(V_t)}",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 4,
    "min_warmup_bars": 20,
    "notes": "Rolling volume-weighted intraday bar position. High values indicate accumulation pressure; low values indicate distribution pressure.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    intraday_position = safe_div(close - low, high - low)
    volume_weighted_position = volume * intraday_position

    numerator = ts_mean(volume_weighted_position, 20)
    denominator = ts_mean(volume, 20)

    return safe_div(numerator, denominator)