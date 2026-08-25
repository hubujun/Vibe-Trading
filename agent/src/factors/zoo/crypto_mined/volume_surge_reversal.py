"""Crypto volume surprise reversal factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_std, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_surge_reversal",
    "nickname": "Volume Surge Reversal",
    "theme": ["volume"],
    "formula_latex": r"-zscore\left(\frac{V_t - V_{t-1}}{\sigma_{V,20}}\right) \cdot zscore\left(\frac{C_t - C_{t-1}}{C_{t-1}}\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 21,
    "notes": "Contrarian reaction to a one-period volume surge, standardized cross-sectionally.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    ret = safe_div(delta(close, 1), close.shift(1))
    volume_surge = safe_div(delta(volume, 1), ts_std(volume, 20))

    return -zscore(volume_surge) * zscore(ret)