"""Crypto mined volume flow asymmetry."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_asymmetry",
    "nickname": "VolumeFlowAsym",
    "theme": ["volume"],
    "formula_latex": r"zscore\left(\frac{\mathrm{MA}_{10}\left(V_t \cdot \frac{2C_t-H_t-L_t}{H_t-L_t}\right)}{\mathrm{MA}_{10}(V_t)}\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 10,
    "notes": "Volume weighted by the close position within the high-low range, averaged over 10 bars and normalized by average volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    rng = high - low
    close_position = safe_div(2.0 * close - high - low, rng)
    signed_volume = volume * close_position
    flow = ts_mean(signed_volume, 10)
    avg_volume = ts_mean(volume, 10)
    normalized_flow = safe_div(flow, avg_volume)
    return zscore(normalized_flow)