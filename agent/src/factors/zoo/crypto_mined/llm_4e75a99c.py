from __future__ import annotations
# auto-injected imports (factor_miner)
from src.factors.base import rank, zscore, ts_rank, ts_corr, ts_cov, ts_mean, ts_std, ts_max, ts_min, ts_argmax, ts_argmin, delta, decay_linear, safe_div, signed_power, scale, vwap


import pandas as pd
import numpy as np


__alpha_meta__ = {
    "id": "crypto_mined_vol_adjusted_flow_persistence",
    "nickname": "vol_adj_flow_persistence",
    "theme": ["microstructure"],
    "formula_latex": r"rank\left( \frac{|r_t|}{\sigma_t} \times sign(r_t) \times ts\_rank\left( \frac{V_t}{\sigma_V} + |r_t|, 5 \right) \right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 25,
    "notes": "Captures persistent directional flow adjusted for volatility using volume-price interaction.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    # Returns
    ret = safe_div(delta(close, 1), close.shift(1))

    # Volatility- scaled return (z-score of returns)
    ret_vol = ts_std(ret, 20)
    vol_adj_ret = safe_div(ret, ret_vol)

    # Volume volatility
    vol_vol = ts_std(volume, 20)
    vol_adj_volume = safe_div(volume, vol_vol)

    # Flow persistence: combine volume and absolute return
    flow = vol_adj_volume + np.abs(vol_adj_ret)
    flow_persistence = ts_rank(flow, 5)

    # Directional signal: signed vol-adjusted return
    direction = np.sign(vol_adj_ret)

    # Combine: persistence of directional flow
    combined = direction * flow_persistence * np.abs(vol_adj_ret)

    # Cross-sectional rank
    return rank(combined)