"""crypto VOLUME: volume-weighted range position."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_range",
    "nickname": "量能加权区间位置",
    "theme": ["volume"],
    "formula_latex": "\\frac{\\sum_{i=0}^{n-1} V_{t-i} \\cdot \\mathrm{loc}_{t-i}}{\\sum_{i=0}^{n-1} V_{t-i}}, \\quad \\mathrm{loc}=\\frac{C-L}{H-L}",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 7,
    "min_warmup_bars": 10,
    "notes": "Rolling volume-weighted position of close within the high-low range; high values indicate buying pressure.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return cross-sectional rank of volume-weighted intraday range position."""
    close = panel["close"].astype(float)
    high = panel["high"].reindex(index=close.index, columns=close.columns).astype(float)
    low = panel["low"].reindex(index=close.index, columns=close.columns).astype(float)
    volume = panel["volume"].reindex(index=close.index, columns=close.columns).astype(float)

    loc = safe_div(close - low, high - low)
    weighted = volume * loc
    numerator = ts_mean(weighted, 10)
    denominator = ts_mean(volume, 10)
    raw = safe_div(numerator, denominator)
    return rank(raw)