"""crypto VOLUME: volume-ranked return thrust, cross-sectionally reversed."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_thrust_reversal",
    "nickname": "成交量加权推力反转",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{zscore}\\!\\left(\\mathrm{tsmean}_{3}\\!\\left(\\frac{\\Delta C_t}{C_{t-1}}\\cdot \\mathrm{tsrank}_{20}(V_t)\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 24,
    "notes": (
        "Weighted return thrust: daily returns multiplied by the 20-bar time-series "
        "rank of volume, then averaged over 3 bars and negated. High-volume up-moves "
        "that are not confirmed tend to reverse; the factor fades recent volume-backed "
        "thrust cross-sectionally."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated cross-sectional z-score of volume-weighted return thrust."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    vol_rank = ts_rank(volume, 20)
    thrust = ts_mean(ret * vol_rank, 3)
    return -zscore(thrust)