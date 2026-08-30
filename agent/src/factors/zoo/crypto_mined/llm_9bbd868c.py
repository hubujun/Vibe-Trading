from __future__ import annotations
# auto-injected imports (factor_miner)
from src.factors.base import rank, zscore, ts_rank, ts_corr, ts_cov, ts_mean, ts_std, ts_max, ts_min, ts_argmax, ts_argmin, delta, decay_linear, safe_div, signed_power, scale, vwap


import pandas as pd
import numpy as np


__alpha_meta__ = {
    "id": "crypto_mined_vol_skew_momentum",
    "nickname": "vol_skew_momentum",
    "theme": ["volatility", "momentum"],
    "formula_latex": r"rank\left( -\Delta_{5}\left( \frac{std_{10}(r)}{std_{20}(r)} \right) \times \Delta_{5}\left( \frac{C_t}{C_{t-20}} \right) \right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 25,
    "notes": "Combines short-term to long-term volatility ratio change (volatility regime shift) with momentum, betting on coins where volatility is compressing while price is rising.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    prev_close = close - delta(close, 1)
    ret = safe_div(delta(close, 1), prev_close)

    # Short-term and long-term volatility
    std_short = ts_std(ret, 10)
    std_long = ts_std(ret, 20)
    vol_ratio = safe_div(std_short, std_long)

    # Change in volatility ratio over 5 days (volatility compression/expansion)
    vol_ratio_change = delta(vol_ratio, 5)

    # Momentum: 20-day return
    mom = safe_div(close, delta(close, 20)) - 1

    # Combine: negative vol ratio change (compression) + positive momentum
    combined = -vol_ratio_change * mom

    # Cross-sectional rank
    return rank(combined)