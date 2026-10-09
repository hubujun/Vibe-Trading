"""crypto REVERSAL: overbought reversal amplified by volatility expansion."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_rank, ts_std, zscore

__alpha_meta__ = {
    "id": "crypto_mined_vol_expansion_reversal",
    "nickname": "波动扩张反转",
    "theme": ["reversal", "volatility"],
    "formula_latex": "-\\mathrm{ts\\_rank}(C,20)\\cdot\\mathrm{zscore}\\!\\left(\\frac{\\mathrm{ts\\_std}(C,5)}{\\mathrm{ts\\_std}(C,60)}\\right)",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 60,
    "notes": "Short-horizon reversal: negative when price sits near the top of its 20d range and short-term volatility has expanded versus long-term.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Reversal signal conditioned on cross-sectional volatility expansion."""
    close = panel["close"].astype(float)

    tr = ts_rank(close, 20)
    short_vol = ts_std(close, 5)
    long_vol = ts_std(close, 60)
    vol_ratio = safe_div(short_vol, long_vol)
    cond = zscore(vol_ratio)
    return -1.0 * tr * cond