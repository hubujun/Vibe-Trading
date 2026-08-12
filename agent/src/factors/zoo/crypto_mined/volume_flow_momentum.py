"""Crypto momentum: volume-weighted close location inside daily range."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_rank, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_momentum",
    "nickname": "Volume location momentum",
    "theme": ["momentum"],
    "formula_latex": r"F_t=\mathrm{rank}\left(\mathrm{ts\_rank}\left(\frac{\mathrm{ts\_mean}(V_t(2C_t-H_t-L_t)/(H_t-L_t),10)}{\mathrm{ts\_std}(V_t(2C_t-H_t-L_t)/(H_t-L_t),10)},20\right)\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 30,
    "notes": "Uses the location of close inside the high-low range, volume-weighting it to measure conviction, and ranks the rolling stability of that flow.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    loc = safe_div(2.0 * close - high - low, high - low)
    flow = loc * volume

    flow_mean = ts_mean(flow, 10)
    flow_std = ts_std(flow, 10)
    flow_score = safe_div(flow_mean, flow_std)

    return rank(ts_rank(flow_score, 20))