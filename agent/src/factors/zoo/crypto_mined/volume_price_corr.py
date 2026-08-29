"""Crypto mined factor: volume-price correlation with volume rank."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_corr, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_corr",
    "nickname": "volume_price_corr",
    "theme": ["volume"],
    "formula_latex": r"rank\left( Corr_t\left(r_t, V_t\right) \times ts\_rank\left(V_t, 20\right) \right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 25,
    "notes": "Cross-sectional rank of rolling volume-price correlation scaled by rolling volume rank.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    prev_close = close - delta(close, 1)
    ret = safe_div(delta(close, 1), prev_close)

    corr = ts_corr(ret, volume, 20)
    vol_rank = ts_rank(volume, 20)

    return rank(corr * vol_rank)