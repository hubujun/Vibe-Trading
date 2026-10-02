"""crypto VOLUME: Amihud-style illiquidity, cross-sectionally standardised.

Price impact per unit of traded value: squared return divided by dollar
volume, smoothed over 20 bars and z-scored across instruments. Negated so
that highly illiquid names receive a negative score (liquidity premium tilt).
"""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, signed_power, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_amihud_illiq_z",
    "nickname": "Amihud非流动性",
    "theme": ["volume"],
    "formula_latex": (
        "-\\,z\\!\\left(\\overline{\\frac{r_t^{2}}{C_t \\cdot V_t}}\\right)_{20}"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 21,
    "notes": (
        "Amihud illiquidity proxy: safe_div(signed_power(ret,2), close*volume) "
        "averaged over 20 bars, then z-scored per row. Uses squared return so no "
        "abs/sign primitive is needed; zero dollar volume yields NaN, not inf."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated smoothed Amihud illiquidity z-score."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret1 = safe_div(delta(close, 1), close.shift(1))
    dollar_volume = close * volume

    # |ret|^2 / dollar volume  (safe_div guards zero traded value)
    illiq = safe_div(signed_power(ret1, 2), dollar_volume)
    illiq_smooth = ts_mean(illiq, 20)

    return -zscore(illiq_smooth)