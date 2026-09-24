"""crypto MICROSTRUCTURE: intrabar closing pressure within the daily range."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_intrabar_pressure",
    "nickname": "日内收盘压力",
    "theme": ["microstructure", "momentum"],
    "formula_latex": "\\mathrm{tsmean}_{10}\\!\\left(\\frac{C_t - \\tfrac{H_t+L_t}{2}}{H_t - L_t}\\right)",
    "columns_required": ["high", "low", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 12,
    "notes": "Where close sits inside the bar range; smoothed, positive implies persistent buying pressure.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return smoothed normalized close position within the daily range."""
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    close = panel["close"].astype(float)

    rng = high - low
    mid = (high + low) / 2.0
    pos = safe_div(close - mid, rng + 1e-12)
    return ts_mean(pos, 10)