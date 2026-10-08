"""crypto VOLUME: volume-weighted price location."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_price_location",
    "nickname": "量价位置累积",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{zscore}\left(\frac{\operatorname{ts\_mean}\left(\mathrm{volume} \cdot \frac{(\mathrm{close}-\mathrm{low}) - (\mathrm{high}-\mathrm{close})}{\mathrm{high}-\mathrm{low}}, 20\right)}{\operatorname{ts\_mean}(\mathrm{volume}, 20)}\right)",
    "columns_required": ["high", "low", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "Volume-weighted close location within daily range, averaged over 20 bars. Positive when close tends to sit near highs on high-volume bars.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-weighted price location, aligned to close index."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_ = high - low
    mfm = safe_div((close - low) - (high - close), range_)
    weighted = mfm * volume
    num = ts_mean(weighted, 20)
    den = ts_mean(volume, 20)
    return zscore(safe_div(num, den))