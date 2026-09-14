"""crypto VOLUME: volume-absolute-return correlation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_vol_corr",
    "nickname": "量波相关性",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{ts\\_corr}\\left(\\frac{\\Delta V_t}{V_{t-1}}, |r_t|, 20\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "Negative rolling correlation between volume growth and absolute return; captures volume-driven volatility clustering as a reversal signal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-absolute-return correlation factor."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    prev_close = close.shift(1)
    prev_volume = volume.shift(1)
    returns = safe_div(delta(close, 1), prev_close)
    vol_growth = safe_div(delta(volume, 1), prev_volume)
    return -ts_corr(vol_growth, returns.abs(), 20)