from __future__ import annotations
# auto-injected imports (factor_miner)
from src.factors.base import rank, zscore, ts_rank, ts_corr, ts_cov, ts_mean, ts_std, ts_max, ts_min, ts_argmax, ts_argmin, delta, decay_linear, safe_div, signed_power, scale, vwap

"""Crypto cross-sectional alpha: volume-weighted volatility-of-volume momentum."""


import pandas as pd


__alpha_meta__ = {
    "id": "crypto_mined_vol_of_volume_momentum",
    "nickname": "Volume Volatility Momentum",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{rank}\left( ts\_rank(ts\_std(V,10)/ts\_mean(V,10), 20) \cdot \mathrm{sign}(ts\_corr(C,V,5)) \right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": (
        "Cross-sectional factor combining the rolling rank of volume-of-volume "
        "(instability of turnover) with the short-horizon sign of price-volume "
        "co-movement. High score = rising, unstable turnover accompanied by "
        "positive price-volume feedback; low score = falling, unstable turnover "
        "with negative feedback. Market-neutral via cross-sectional rank."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    # Coefficient of variation of volume over 10 bars -> instability of turnover
    vol_mean = ts_mean(volume, 10)
    vol_std = ts_std(volume, 10)
    vol_cv = safe_div(vol_std, vol_mean + 1e-12)

    # Where does current turnover-instability sit in its own 20d history?
    vol_instability_rank = ts_rank(vol_cv, 20)

    # Short-horizon price-volume co-movement direction
    pv_corr = ts_corr(close, volume, 5)
    pv_sign = pv_corr.apply(lambda s: s.clip(lower=0) * 0 + (s > 0).astype(float) - (s < 0).astype(float))

    # Combine: rising instability * sign of co-movement
    signal = vol_instability_rank * pv_sign

    return rank(signal)