"""Crypto volume: rolling volume-return correlation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.factors.base import delta, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr",
    "nickname": "量价相关性",
    "theme": ["volume"],
    "formula_latex": r"zscore\left(\rho_{20}\left(\Delta \log C_t, \Delta \log V_t\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "Cross-sectional z-score of rolling correlation between daily log return and daily log volume change.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    log_close = np.log(close)
    log_volume = np.log1p(volume)

    ret = delta(log_close, 1)
    vol_ret = delta(log_volume, 1)

    corr = ts_corr(ret, vol_ret, 20)
    return zscore(corr)