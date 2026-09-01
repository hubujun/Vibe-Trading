"""crypto VOLUME: price-range absorption flow."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, decay_linear, ts_rank, rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_flow",
    "nickname": "VolumeRangeFlow",
    "theme": ["volume"],
    "formula_latex": "\\text{rank}_t\\left(\\text{ts\\_rank}_{20}\\left(\\text{decay\\_linear}_5\\left(\\frac{\\text{volume}}{\\text{high}-\\text{low}}\\right)\\right)\\right)",
    "columns_required": ["volume", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 25,
    "notes": "Volume absorbed per unit price range, smoothed and ranked.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-per-range flow rank."""
    volume = panel["volume"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)

    flow = safe_div(volume, high - low)
    smoothed = decay_linear(flow, 5)
    return rank(ts_rank(smoothed, 20))