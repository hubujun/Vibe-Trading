"""crypto VOLUME: volume-confirmed momentum reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_vol_mom_reversal",
    "nickname": "量价动量反转",
    "theme": ["volume"],
    "formula_latex": "-z\\big(\\mathrm{ts\\_mean}(r,20)\\cdot \\frac{\\mathrm{ts\\_mean}(V,5)}{\\mathrm{ts\\_mean}(V,30)}\\big)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 31,
    "notes": "20-day momentum scaled by short/long volume surge, then cross-sectionally z-scored and negated. Trending names on elevated relative volume are crowded and prone to reversal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Volume-conditioned momentum reversal signal aligned to close index."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret1 = safe_div(delta(close, 1), close.shift(1))
    mom = ts_mean(ret1, 20)
    vol_short = ts_mean(volume, 5)
    vol_long = ts_mean(volume, 30)
    vol_surge = safe_div(vol_short, vol_long)
    return -zscore(mom * vol_surge)