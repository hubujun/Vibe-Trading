"""crypto VOLATILITY: range-compression squeeze with breakout position."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_max, ts_mean, ts_min, zscore

__alpha_meta__ = {
    "id": "crypto_mined_range_squeeze_break",
    "nickname": "波动压缩突破",
    "theme": ["volatility"],
    "formula_latex": (
        "-z\\left(\\frac{\\overline{R}_5}{\\overline{R}_{20}}\\right)"
        "\\cdot\\left(\\frac{C_t - \\min(L,10)}{\\max(H,10) - \\min(L,10)} - 0.5\\right),"
        "\\quad R_t = \\frac{H_t - L_t}{C_t}"
    ),
    "columns_required": ["high", "low", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 20,
    "notes": (
        "Short-horizon (5d) high-low range relative to long-horizon (20d) range "
        "measures volatility compression; the cross-sectional z-score is negated "
        "so compressed names get a positive loading. This is multiplied by the "
        "close's position inside its 10-bar high-low channel, so a squeeze that "
        "resolves to the top of the range scores highest (and vice versa)."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volatility-squeeze breakout score."""
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    close = panel["close"].astype(float)

    rng = safe_div(high - low, close)
    squeeze = safe_div(ts_mean(rng, 5), ts_mean(rng, 20))
    squeeze_z = zscore(squeeze)

    roll_low = ts_min(low, 10)
    roll_high = ts_max(high, 10)
    position = safe_div(close - roll_low, roll_high - roll_low)

    return -squeeze_z * (position - 0.5)