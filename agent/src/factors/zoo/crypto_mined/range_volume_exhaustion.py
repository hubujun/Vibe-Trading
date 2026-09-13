"""crypto VOLATILITY/VOLUME/REVERSAL: range expansion on volume as exhaustion."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_range_volume_exhaustion",
    "nickname": "区间放量衰竭",
    "theme": ["volatility", "volume", "reversal"],
    "formula_latex": "-\\left(\\mathrm{rank}(\\mathrm{decay\\_linear}(\\frac{\\mathrm{high}-\\mathrm{low}}{\\mathrm{close}},5))-0.5\\right) \\cdot \\left(\\mathrm{ts\\_rank}(\\mathrm{volume},20)-0.5\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "High-low range expansion on high volume as exhaustion reversal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return range/volume exhaustion score aligned to close."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_rel = safe_div(high - low, close)
    smoothed_range = decay_linear(range_rel, 5)
    range_rank = rank(smoothed_range)
    volume_rank = ts_rank(volume, 20)

    return -1.0 * (range_rank - 0.5) * (volume_rank - 0.5)