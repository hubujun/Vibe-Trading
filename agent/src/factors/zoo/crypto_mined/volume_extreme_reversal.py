"""Crypto factor: volume-confirmed reversal from intraday range extremes."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_extreme_reversal",
    "nickname": "volume_extreme_reversal",
    "theme": ["reversal"],
    "formula_latex": r"zscore\left(-\mathrm{MA}_5\left[\left(\frac{C_t-L_t}{H_t-L_t}-0.5\right)\frac{V_t}{\mathrm{MA}_{20}(V_t)}\right]\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 24,
    "notes": "When volume confirms an extreme close location inside the daily range, the next trend tends to reverse. Warmup NaNs are propagated.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_pos = safe_div(close - low, high - low)
    rel_vol = safe_div(volume, ts_mean(volume, 20))
    pressure = (range_pos - 0.5) * rel_vol

    return zscore(-ts_mean(pressure, 5))