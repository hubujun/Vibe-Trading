"""Volume-confirmed price return correlation, cross-sectionally z-scored."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr_z",
    "nickname": "VolumeReturnCorr",
    "theme": ["volume"],
    "formula_latex": "\\text{zscore}_t\\left(\\rho_{20}\\left(\\frac{\\Delta C}{C_{-1}}, V\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 0,
    "min_warmup_bars": 21,
    "notes": "Cross-sectional z-score of rolling volume-return correlation.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the cross-sectional z-score of the 20-bar volume-return correlation."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    returns = safe_div(delta(close, 1), close.shift(1))
    corr = ts_corr(returns, volume, 20)

    return zscore(corr)