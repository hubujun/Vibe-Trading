"""crypto mined range volume liquidity: negative z-score of normalized range per relative volume."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_range_volume_liquidity",
    "nickname": "RangeVolumeLiquidity",
    "theme": ["liquidity", "volatility"],
    "formula_latex": "-\\mathrm{decay\\_linear}\\left(\\mathrm{zscore}\\left(\\frac{(high-low)/close}{volume/\\mathrm{ts\\_mean}(volume,10)}\\right), 3\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "A high normalized range per unit relative volume indicates poor liquidity / high price impact. The negative z-score makes liquid, low-impact assets score higher.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    rng = safe_div(high - low, close)
    rel_vol = safe_div(volume, ts_mean(volume, 10))
    impact = safe_div(rng, rel_vol)

    raw = -zscore(impact)
    return decay_linear(raw, 3)