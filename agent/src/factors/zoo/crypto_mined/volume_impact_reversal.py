"""crypto VOLUME: volume-return covariance reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_cov, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_impact_reversal",
    "nickname": "量价冲击反转",
    "theme": ["volume"],
    "formula_latex": "-\\frac{\\mathrm{ts\\_cov}(V_t, (C_t - C_{t-1})/C_t, 6)}{\\mathrm{ts\\_std}(V_t, 6)}",
    "columns_required": ["volume", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 6,
    "min_warmup_bars": 7,
    "notes": "Negative covariance between volume and returns, normalized by volume volatility. Captures volume-driven overreaction reversal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return negative volume-return covariance scaled by volume volatility."""
    volume = panel["volume"].astype(float)
    close = panel["close"].astype(float)
    returns = safe_div(delta(close, 1), close)
    cov = ts_cov(volume, returns, 6)
    vol_std = ts_std(volume, 6)
    return -safe_div(cov, vol_std)