from __future__ import annotations
# auto-injected imports (factor_miner)
from src.factors.base import rank, zscore, ts_rank, ts_corr, ts_cov, ts_mean, ts_std, ts_max, ts_min, ts_argmax, ts_argmin, delta, decay_linear, safe_div, signed_power, scale, vwap

"""Crypto cross-sectional alpha: volume-shock convexity with return asymmetry."""


import pandas as pd


__alpha_meta__ = {
    "id": "crypto_mined_volume_shock_convexity",
    "nickname": "Volume Shock Convexity",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{rank}\left( -\, \mathrm{corr}_{20}\!\left(r_t,\; \tilde{V}_t\right) \cdot \mathrm{sign}\!\left(r_t\right)\cdot \frac{\tilde{V}_t}{\mathrm{std}_{20}(\tilde{V})} \right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": (
        "Captures asymmetric volume response: for each bar, the signed volume shock "
        "(V / rolling mean) is weighted by the sign of the return, then correlated "
        "over a 20d window against returns. A negative correlation means up-moves "
        "come on light volume and down-moves on heavy volume (distribution), which "
        "we fade cross-sectionally. Ranked long-short, dollar neutral."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))

    vol_mean = ts_mean(volume, 20)
    vol_std = ts_std(volume, 20)
    vol_shock = safe_div(volume - vol_mean, vol_std)

    signed_shock = vol_shock * ret.apply(lambda s: s.where(s >= 0, -1.0).fillna(0.0))

    asy_corr = ts_corr(ret, signed_shock, 20)

    return rank(-asy_corr)