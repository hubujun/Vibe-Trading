"""Crypto mined volume factor: volume time-series-rank confirmed momentum."""
from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_tsrank_confirmed_momentum",
    "nickname": "VolTrendConfirm",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{zscore}\left(DWMA_{8}\left(\operatorname{ts\_rank}(V,20)(\operatorname{ts\_rank}(C,10)-0.5)\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 8,
    "min_warmup_bars": 35,
    "notes": "Combines rolling volume percentile with rolling close-price percentile. High volume in the upper part of the trailing range is positive; high volume in the lower part is negative.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    volume_pressure = ts_rank(volume, 20)
    price_side = ts_rank(close, 10) - 0.5
    raw_signal = volume_pressure * price_side
    smoothed_signal = decay_linear(raw_signal, 8)

    return zscore(smoothed_signal)