"""crypto VOLUME: volume-weighted close location momentum."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_clv_momentum",
    "nickname": "量价加权收盘位置动量",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\frac{\\mathrm{ts\\_mean}(\\mathrm{CLV} \\cdot V, 10)}{\\mathrm{ts\\_mean}(V, 10)}\\right) - 0.5, \\quad \\mathrm{CLV} = \\frac{(C-L)-(H-C)}{H-L}",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 10,
    "notes": "Volume-weighted close location value over 10 bars; positive when closes are near highs on high volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)
    clv = safe_div((close - low) - (high - close), high - low)
    weighted = clv * volume
    signal = safe_div(ts_mean(weighted, 10), ts_mean(volume, 10))
    return rank(signal) - 0.5