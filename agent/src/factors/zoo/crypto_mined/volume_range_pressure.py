"""crypto VOLUME: volume-weighted close location pressure."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_pressure",
    "nickname": "Volume Range Pressure",
    "theme": ["volume"],
    "formula_latex": r"z\left(\mathrm{MA}_{20}\left(V_t \cdot \frac{2C_t - H_t - L_t}{H_t - L_t}\right)\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 21,
    "notes": "Volume-weighted close location value over 20 bars, standardized cross-sectionally.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-weighted range pressure aligned to close."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float).reindex_like(close)
    low = panel["low"].astype(float).reindex_like(close)
    volume = panel["volume"].astype(float).reindex_like(close)

    clv = safe_div(2.0 * close - high - low, high - low)
    mfv = clv * volume

    return zscore(ts_mean(mfv, 20))