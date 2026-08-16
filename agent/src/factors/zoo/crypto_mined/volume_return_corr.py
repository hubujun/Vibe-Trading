"""crypto_mined_volume_return_corr: rolling volume-return correlation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr",
    "nickname": "VolumeReturnCorr",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{zscore}\left(\rho_{20}\left(\mathrm{volume}_t, \Delta \mathrm{close}_t\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 23,
    "notes": "Cross-sectional z-score of rolling 20-bar correlation between volume and close-to-close returns.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    corr = ts_corr(volume, delta(close, 1), 20)
    smoothed = decay_linear(corr, 3)
    return zscore(smoothed)