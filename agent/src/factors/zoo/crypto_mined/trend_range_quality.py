"""crypto MOMENTUM: volatility-adjusted drift gated by position in range."""

from __future__ import annotations

import pandas as pd

from src.factors.base import (
    decay_linear,
    delta,
    safe_div,
    ts_max,
    ts_mean,
    ts_min,
    ts_std,
    zscore,
)

__alpha_meta__ = {
    "id": "crypto_mined_trend_range_quality",
    "nickname": "波动调整趋势区间质量",
    "theme": ["momentum"],
    "formula_latex": (
        "z\\left(\\mathrm{decay}_5\\left("
        "\\frac{\\overline{r}_{21}}{\\sigma(r)_{21}}"
        "\\cdot\\left(\\frac{C_t-\\min_{42}(C)}{\\max_{42}(C)-\\min_{42}(C)}-0.5\\right)"
        "\\right)\\right),\\; r_t=\\frac{\\Delta_1 C_t}{C_{t-1}}"
    ),
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 7,
    "min_warmup_bars": 43,
    "notes": (
        "Trend-quality momentum: the 21-bar Sharpe of daily returns (drift "
        "divided by realised vol) is multiplied by how high the price sits "
        "inside its own 42-bar high/low envelope. Trends that are both smooth "
        "(high Sharpe) and confirmed by range location get the largest weight; "
        "noisy drifts near the bottom of the range are suppressed. Smoothed by "
        "a 5-bar linear decay and cross-sectionally z-scored."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Risk-adjusted drift times normalised position within the price range."""
    close = panel["close"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    drift = ts_mean(ret, 21)
    vol = ts_std(ret, 21)
    sharpe = safe_div(drift, vol)

    hi = ts_max(close, 42)
    lo = ts_min(close, 42)
    range_pos = safe_div(close - lo, hi - lo) - 0.5

    return zscore(decay_linear(sharpe * range_pos, 5))