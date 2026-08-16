"""Crypto volume-return correlation drift factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr_drift",
    "nickname": "VolRetCorrDrift",
    "theme": ["volume"],
    "formula_latex": r"zscore\left(\Delta_5 \mathrm{Corr}_{20}(V, \Delta C)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 26,
    "notes": "Measures the 5-bar change in the rolling 20-bar correlation between volume and one-bar price change. Positive drift signals strengthening volume confirmation of price moves.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    vol_ret_corr = ts_corr(volume, delta(close, 1), 20)
    corr_drift = delta(vol_ret_corr, 5)
    return zscore(corr_drift)