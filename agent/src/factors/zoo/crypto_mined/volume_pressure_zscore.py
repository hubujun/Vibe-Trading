"""Crypto volume-weighted price pressure factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_pressure_zscore",
    "nickname": "Volume Pressure Rank",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{rank}\left( \mathrm{DWMA}_3\left[ \frac{\mathrm{SMA}_{20}\left(V_t (2 \frac{C_t-L_t}{H_t-L_t} - 1)\right)}{\sigma_{20}\left(V_t (2 \frac{C_t-L_t}{H_t-L_t} - 1)\right)} \right] \right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 22,
    "notes": "Volume-weighted close location inside the day's range; high values indicate buying-pressure dominance.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float).reindex(index=close.index, columns=close.columns)
    low = panel["low"].astype(float).reindex(index=close.index, columns=close.columns)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    rng = (high - low) + 1e-12
    close_loc = safe_div(close - low, rng)
    flow = volume * (2.0 * close_loc - 1.0)

    flow_ma = ts_mean(flow, 20)
    flow_std = ts_std(flow, 20)
    pressure_z = safe_div(flow_ma, flow_std + 1e-12)

    return rank(decay_linear(pressure_z, 3))