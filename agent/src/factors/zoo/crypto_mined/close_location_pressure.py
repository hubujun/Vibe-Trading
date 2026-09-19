"""crypto MICROSTRUCTURE: smoothed close location value (buy-side pressure)."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_close_location_pressure",
    "nickname": "收盘位置买压",
    "theme": ["microstructure"],
    "formula_latex": (
        "D_5\\!\\left(\\mathrm{MA}_{10}"
        "\\!\\left(\\frac{C_t - L_t}{H_t - L_t}\\right) - 0.5\\right)"
    ),
    "columns_required": ["close", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 15,
    "notes": (
        "Average position of the close inside each bar's high-low range "
        "(close location value), centered at 0.5 and linearly decayed. "
        "Persistently high values indicate buyers closing bars near the "
        "high, a continuation-oriented buy-pressure proxy."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the decayed, smoothed close-location pressure signal."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)

    clv = safe_div(close - low, high - low)
    pressure = ts_mean(clv, 10) - 0.5

    return decay_linear(pressure, 5)