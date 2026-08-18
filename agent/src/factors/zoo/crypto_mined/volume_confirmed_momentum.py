"""crypto volume-confirmed momentum factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_confirmed_momentum",
    "nickname": "VolumeConfirmedMomentum",
    "theme": ["volume", "momentum"],
    "formula_latex": "zscore\\left(\\left(\\frac{C_t}{\\mathrm{mean}_{20}(C_t)} - 1\\right) \\cdot \\rho_{20}\\left(\\frac{\\Delta C_t}{C_{t-1}}, \\Delta V_t\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 20,
    "notes": "Price momentum scaled by the rolling correlation between return and volume change.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    dvol = delta(volume, 1)
    price_trend = safe_div(close, ts_mean(close, 20)) - 1.0
    volume_confirm = ts_corr(ret, dvol, 20)

    raw = price_trend * volume_confirm

    return zscore(raw)