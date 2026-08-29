"""Crypto volume-weighted order-flow acceleration."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_acceleration",
    "nickname": "VolumeFlowAccel",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}_{ts}\\left(\\mathrm{decay}_5\\left(z\\left(\\Delta \\frac{V(2C-H-L)}{H-L}\\right)\\right),20\\right)",
    "columns_required": ["high", "low", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Volume-weighted bar imbalance acceleration, cross-sectionally standardized, smoothed, and time-series ranked.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return time-series rank of volume-flow acceleration."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float).reindex(index=close.index, columns=close.columns)
    low = panel["low"].astype(float).reindex(index=close.index, columns=close.columns)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    range_span = high - low
    flow = volume * safe_div(2.0 * close - high - low, range_span)
    acceleration = delta(flow, 1)
    standardized = zscore(acceleration)
    smoothed = decay_linear(standardized, 5)
    return ts_rank(smoothed, 20)