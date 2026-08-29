"""Volume-weighted return skewness proxy factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, signed_power, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_return_skew",
    "nickname": "VolumeReturnSkew",
    "theme": ["volume"],
    "formula_latex": "\\text{zscore}_t\\left(\\frac{\\mathrm{MA}_{20}\\left(V \\cdot \\mathrm{sgn}(R)|R|^3\\right)}{\\mathrm{MA}_{20}(V)}\\right),\\; R_t=\\Delta C_t/C_{t-1}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 0,
    "min_warmup_bars": 21,
    "notes": "Volume-weighted third-moment proxy of returns, cross-sectionally normalized.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the cross-sectional z-score of volume-weighted return skewness."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    returns = safe_div(delta(close, 1), close.shift(1))
    return_shock = signed_power(returns, 3)

    weighted_shock = ts_mean(volume * return_shock, 20)
    avg_volume = ts_mean(volume, 20)
    skew_proxy = safe_div(weighted_shock, avg_volume)

    return zscore(skew_proxy)