"""Crypto VOLUME: smoothed volume-adjusted price impact, inverted."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_illiquidity_impact",
    "nickname": "量价冲击",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(-\\mathrm{ts\\_rank}_{20}\\left(\\mathrm{ts\\_mean}_{20}\\left(\\frac{(\\Delta C)^{2}}{V}\\right)\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 0,
    "min_warmup_bars": 40,
    "notes": "Rewards liquid coins with low smoothed squared-return-per-volume; the sign inversion makes high rank mean strong volume absorption.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Volume-adjusted price impact tilt."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    daily_ret = delta(close, 1)
    daily_ret_sq = daily_ret * daily_ret
    price_impact = safe_div(daily_ret_sq, volume)
    smooth_impact = ts_mean(price_impact, 20)
    impact_rank = ts_rank(smooth_impact, 20)

    return rank(-impact_rank)