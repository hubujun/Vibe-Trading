"""crypto mined volume-weighted intraday pressure."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_intraday_pressure",
    "nickname": "Volume Intraday Pressure",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{zscore}\left(\frac{\mathrm{mean}_{20}\left(V_t\cdot \frac{C_t-L_t-(H_t-C_t)}{H_t-L_t}\right)}{\mathrm{mean}_{20}(V_t)}\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 4,
    "min_warmup_bars": 21,
    "notes": "Volume-weighted close location within the day's range, normalized by average volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    close_location = safe_div(close - low - (high - close), high - low)
    volume_flow = volume * close_location

    avg_flow = ts_mean(volume_flow, 20)
    avg_volume = ts_mean(volume, 20)
    normalized_flow = safe_div(avg_flow, avg_volume)

    return zscore(normalized_flow)