"""Crypto mined microstructure: range-location volume flow."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, ts_corr, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_microstructure_range_volume_flow",
    "nickname": "Range Location Flow",
    "theme": ["microstructure"],
    "formula_latex": r"R_t=\frac{H_t-L_t}{C_t},\quad L_t=\frac{C_t-L_t}{H_t-L_t},\quad F=\mathrm{rank}(\mathrm{decay\_linear}(\mathrm{ts\_mean}(L_t,10)\cdot\mathrm{ts\_rank}(R_t,20)\cdot\mathrm{ts\_corr}(L_t,V_t,10),5))",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 30,
    "notes": "Close location inside the daily range, confirmed by volume correlation and range expansion, smoothed with a 5-bar linear decay.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    daily_range = safe_div(high - low, close)
    close_location = safe_div(close - low, high - low)

    location_trend = ts_mean(close_location, 10)
    range_regime = ts_rank(daily_range, 20)
    volume_flow = ts_corr(close_location, volume, 10)

    core = location_trend * range_regime * volume_flow
    return rank(decay_linear(core, 5))