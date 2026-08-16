"""crypto volume: volume-weighted close location within daily range."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, decay_linear, rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_imbalance",
    "nickname": "Volume Flow Imbalance",
    "theme": ["volume"],
    "formula_latex": "\\text{flow}_t = \\text{volume}_t \\cdot \\frac{(\\text{close}_t - \\text{low}_t) - (\\text{high}_t - \\text{close}_t)}{\\text{high}_t - \\text{low}_t}",
    "columns_required": ["volume", "high", "low", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 11,
    "notes": "Volume-weighted close location; positive values indicate closing near highs, negative near lows.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the cross-sectional rank of smoothed volume-flow imbalance."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)

    price_range = high - low
    close_loc = (close - low) - (high - close)
    close_loc_norm = safe_div(close_loc, price_range)
    flow = close_loc_norm * volume
    flow_smoothed = decay_linear(flow, 10)
    return rank(flow_smoothed)