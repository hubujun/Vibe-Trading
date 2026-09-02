"""Crypto mined factor: volume-range correlation with volume surge."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, ts_corr, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_response",
    "nickname": "Volume Range Response",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{Rank}_t\left(\mathrm{DL}_3\left( \rho_{10}(\mathrm{Vol}_t, H_t-L_t)\cdot Z_t\left(\frac{\mathrm{Vol}_t}{\mathrm{MA}_{20}(\mathrm{Vol})_t}\right)\right)\right)",
    "columns_required": ["high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 24,
    "notes": "Rolling correlation between volume and high-low range, weighted by a cross-sectional volume surge signal. Uses only current and past bars; NaN warmup is preserved.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    rng = high - low
    vol_corr = ts_corr(volume, rng, 10)
    vol_surge = safe_div(volume, ts_mean(volume, 20))

    raw = vol_corr * zscore(vol_surge)

    return rank(decay_linear(raw, 3))