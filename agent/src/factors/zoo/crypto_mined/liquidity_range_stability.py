"""crypto_mined_liquidity_range_stability."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean, ts_std, zscore

__alpha_meta__ = {
    "id": "crypto_mined_liquidity_range_stability",
    "nickname": "LiquidityRangeStability",
    "theme": ["liquidity"],
    "formula_latex": "\\text{signal} = z\\left(\\frac{V^d_{10}}{V^d_{20}}\\right) - z\\left(\\bar{R}_{10}\\right) - z\\left(\\frac{\\sigma(R_{10})}{\\bar{R}_{10}}\\right) - z\\left(\\frac{\\sigma(V_{10})}{V^d_{20}}\\right),\\quad R=\\frac{H-L}{C}",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "Liquidity proxy favouring high recent volume relative to baseline, with narrow and stable trading ranges and stable volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_pct = safe_div(high - low, close)
    range_mean = ts_mean(range_pct, 10)
    range_cv = safe_div(ts_std(range_pct, 10), range_mean)

    vol_short = decay_linear(volume, 10)
    vol_long = decay_linear(volume, 20)
    volume_ratio = safe_div(vol_short, vol_long)
    vol_cv = safe_div(ts_std(volume, 10), vol_long)

    return zscore(volume_ratio) - zscore(range_mean) - zscore(range_cv) - zscore(vol_cv)