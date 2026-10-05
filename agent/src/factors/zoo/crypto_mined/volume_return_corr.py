"""crypto VOLUME: volume-return correlation reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr",
    "nickname": "量价相关反转",
    "theme": ["volume", "microstructure"],
    "formula_latex": "-\\mathrm{ts\\_corr}\\left(\\frac{\\Delta c_t}{c_{t-1}}, \\Delta v_t, 20\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 22,
    "notes": "Rolling correlation between daily returns and volume changes; strongly positive values indicate volume-chasing rallies that historically revert.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return negated 20-bar correlation between returns and volume changes."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret = safe_div(delta(close, 1), close.shift(1))
    vol_chg = delta(volume, 1)
    corr = ts_corr(ret, vol_chg, 20)
    return -corr