"""crypto VOLUME: rolling correlation between returns and volume growth, negated."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_corr_reversal",
    "nickname": "Volume-Price Correlation Reversal",
    "theme": ["volume"],
    "formula_latex": "-\\operatorname{Corr}_{20}\\left(r_t, \\frac{V_t - V_{t-1}}{V_{t-1}}\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 21,
    "notes": "Negative rolling correlation between daily returns and volume growth; favors divergence between price and volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated 20-bar return-volume growth correlation."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret = safe_div(delta(close, 1), close.shift(1))
    vol_chg = safe_div(delta(volume, 1), volume.shift(1))
    return -ts_corr(ret, vol_chg, 20)