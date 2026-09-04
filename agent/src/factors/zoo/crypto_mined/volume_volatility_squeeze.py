"""Crypto volume-volatility squeeze factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_rank, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_volatility_squeeze",
    "nickname": "VolumeVolSqueeze",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\frac{\\mathrm{ts\\_mean}(V,5)}{\\mathrm{ts\\_mean}(V,20)} \\cdot \\left(1 - \\mathrm{ts\\_rank}_{20}(\\mathrm{ts\\_std}_{10}(H-L))\\right)\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1h"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "High when 5-period volume mean is above its 20-period mean while 20-period rank of 10-period high-low range volatility is low.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-volatility squeeze factor aligned to close."""
    volume = panel["volume"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    close = panel["close"]

    range_vol = ts_std(high - low, 10)
    range_vol_percentile = ts_rank(range_vol, 20)
    volume_ratio = safe_div(ts_mean(volume, 5), ts_mean(volume, 20))

    raw = volume_ratio * (1.0 - range_vol_percentile)
    return rank(raw).reindex(index=close.index, columns=close.columns)