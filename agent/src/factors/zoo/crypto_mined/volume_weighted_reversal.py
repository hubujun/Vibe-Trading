"""Crypto-mined volume-weighted short-term reversal factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_reversal",
    "nickname": "Volume-Weighted Reversal",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\mathrm{decay\\_linear}_5\\left(-r_{1}\\cdot V/\\overline{V}_{20}\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 30,
    "notes": "Amplifies short-term reversal with above-average trading volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the cross-sectional rank of volume-scaled one-day reversal."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    d_close = delta(close, 1)
    close_ret = safe_div(d_close, close - d_close)

    volume_ratio = safe_div(volume, ts_mean(volume, 20))
    score = -close_ret * volume_ratio

    return rank(decay_linear(score, 5))