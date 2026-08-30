"""Crypto mined cross-sectional volume rank acceleration."""

import pandas as pd

from src.factors.base import delta, rank, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_rank_accel",
    "nickname": "VolumeRankAccel",
    "theme": ["volume"],
    "formula_latex": "\\operatorname{ts\\_mean}_5\\left(\\Delta_t \\operatorname{rank}(V)\\right)",
    "columns_required": ["volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 6,
    "notes": "Measures whether an asset's cross-sectional volume percentile is rising over the last five bars.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    volume = panel["volume"].astype(float)

    vol_rank = rank(volume)
    vol_rank_change = delta(vol_rank, 1)

    return ts_mean(vol_rank_change, 5)