"""crypto_mined_volume_close_location: correlation of volume with close location value."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_close_location",
    "nickname": "Volume Close Location",
    "theme": ["volume", "microstructure"],
    "formula_latex": "\\mathrm{rank}\\left(\\rho_{14}\\left(\\frac{2C_t - H_t - L_t}{H_t - L_t}, V_t\\right)\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 4,
    "min_warmup_bars": 15,
    "notes": "Volume confirmation of closing price location within the daily range.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    clv = safe_div(2 * close - high - low, high - low)
    return rank(ts_corr(clv, volume, 14))