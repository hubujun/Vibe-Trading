"""crypto MICROSTRUCTURE: volume-weighted close location value pressure."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_clv_volume_pressure",
    "nickname": "收盘位置量能压力",
    "theme": ["microstructure", "volume"],
    "formula_latex": (
        "\\mathrm{zscore}\\!\\left(\\overline{CLV_t \\cdot \\frac{V_t}{\\overline{V}_{20}}}_{10}\\right),"
        "\\quad CLV_t = \\frac{2C_t - H_t - L_t}{H_t - L_t}"
    ),
    "columns_required": ["high", "low", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 22,
    "notes": (
        "Close location value CLV in [-1,1] measures where the bar closed inside its range; "
        "it is multiplied by the volume shock V/mean(V,20) to build a money-flow style "
        "pressure series, then smoothed over 10 bars and cross-sectionally z-scored."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the smoothed volume-weighted close-location pressure."""
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    bar_range = high - low
    clv = safe_div(2.0 * close - high - low, bar_range)

    vol_base = ts_mean(volume, 20)
    vol_shock = safe_div(volume, vol_base)

    pressure = clv * vol_shock
    raw = ts_mean(pressure, 10)
    return zscore(raw)