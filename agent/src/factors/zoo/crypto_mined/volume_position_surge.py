"""Crypto mined factor: volume-confirmed intraday range participation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_position_surge",
    "nickname": "Volume Range Participation",
    "theme": ["volume"],
    "formula_latex": r"Z_t\left(\sum_{i=0}^{4} w_i\, \mathrm{Rank}_t(\mathrm{Vol}_{t-i})\cdot \frac{C_{t-i}-L_{t-i}}{H_{t-i}-L_{t-i}}\cdot Z_t\left(\frac{\mathrm{Vol}_{t-i}}{\mathrm{MA}_{20}(\mathrm{Vol})_{t-i}}\right)\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 24,
    "notes": "Volume-ranked close position within the high-low range, amplified by cross-sectional volume surge. NaN is not filled; delayed output naturally has NaN warmup.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    vol_ma = ts_mean(volume, 20)
    vol_surge = safe_div(volume, vol_ma)
    intraday_pos = safe_div(close - low, high - low)

    raw = rank(volume) * intraday_pos * zscore(vol_surge)
    smoothed = decay_linear(raw, 5)

    return zscore(smoothed)