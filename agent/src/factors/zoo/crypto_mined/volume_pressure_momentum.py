"""Crypto volume-signed buying/selling pressure momentum."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_std


__alpha_meta__ = {
    "id": "crypto_mined_volume_pressure_momentum",
    "nickname": "Volume Pressure Momentum",
    "theme": ["momentum", "volume"],
    "formula_latex": "R_t = \\frac{C_t-L_t}{H_t-L_t};\\ \\alpha_t=\\mathrm{rank}\\left(\\frac{\\mathrm{mean}_w(V_t(2R_t-1))}{\\mathrm{std}_w(V_t(2R_t-1))}\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 30,
    "notes": "Signs volume by the daily range position to capture persistent buying/selling pressure; cross-sectional rank makes the normalized signed-volume flow comparable across assets.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_position = safe_div(close - low, high - low)
    signed_volume = (2.0 * range_position - 1.0) * volume

    flow_avg = ts_mean(signed_volume, 21)
    flow_std = ts_std(signed_volume, 21)
    flow_score = safe_div(flow_avg, flow_std)

    return rank(flow_score)