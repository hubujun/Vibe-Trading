"""crypto_mined_volume_volatility_divergence: volume expansion vs range expansion."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_corr, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_volatility_divergence",
    "nickname": "Volume Volatility Divergence",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{VolVolDiv}_t = \mathrm{zscore}\left( \frac{V_t}{\mathrm{Mean}_{10}(V_t)} \cdot \mathrm{Corr}_{10}(V_t, H_t - L_t) \right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 20,
    "notes": "High volume relative to its own mean combined with volume/range correlation.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    rng = high - low
    vol_ratio = safe_div(volume, ts_mean(volume, 10))
    vol_range_corr = ts_corr(volume, rng, 10)

    raw = vol_ratio * vol_range_corr
    factor = zscore(raw)

    return factor.reindex(index=close.index, columns=close.columns)