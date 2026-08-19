"""Crypto mined factor: volume pressure inside rolling range."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_max, ts_min, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_pressure",
    "nickname": "RangeVolumePressure",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\mathrm{ts\\_rank}_{20}(V_t) \\cdot \\frac{\\max_{20}(H_t) - C_t}{\\max_{20}(H_t)-\\min_{20}(L_t)}\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "High values mark high recent volume while close is near the bottom of a 20-bar range.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return cross-sectional rank of rolling-range lower volume pressure."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    volume_rank = ts_rank(volume, 20)
    roll_high = ts_max(high, 20)
    roll_low = ts_min(low, 20)

    pressure = volume_rank * safe_div(roll_high - close, roll_high - roll_low)

    return rank(pressure)