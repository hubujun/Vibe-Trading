"""crypto VOLUME: price-volume co-movement confirmed momentum."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, signed_power, ts_corr, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_corr_momentum",
    "nickname": "量价共振动量",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{zscore}\left(\operatorname{signed\_power}\left(\operatorname{ts\_corr}(r_t,\Delta v_t,20),2\right)\times\operatorname{ts\_mean}(r_t,5)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "Momentum is amplified when returns co-move with volume changes; negative co-movement flips the signal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the cross-sectional z-score of volume-correlation-confirmed momentum."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    prev_close = close - delta(close, 1)
    ret = safe_div(delta(close, 1), prev_close)
    prev_volume = volume - delta(volume, 1)
    vol_change = safe_div(delta(volume, 1), prev_volume)
    corr = ts_corr(ret, vol_change, 20)
    mom = ts_mean(ret, 5)
    return zscore(signed_power(corr, 2) * mom)