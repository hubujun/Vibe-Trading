"""crypto REVERSAL/VOLATILITY: range-conditioned short-term reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_range_cond_reversal",
    "nickname": "区间条件反转",
    "theme": ["reversal", "volatility"],
    "formula_latex": "-\\mathrm{zscore}\\left(\\frac{\\Delta C_t}{C_{t-1}}\\right) \\cdot \\mathrm{tsrank}_{20}\\left(\\frac{H_t - L_t}{C_t}\\right)",
    "columns_required": ["close", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 22,
    "notes": "Short-term reversal weighted by how extreme the intraday high-low range is relative to its own 20-bar history.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    ret = safe_div(delta(close, 1), close.shift(1))
    range_pct = safe_div(high - low, close)
    range_rank = ts_rank(range_pct, 20)
    return -zscore(ret) * range_rank