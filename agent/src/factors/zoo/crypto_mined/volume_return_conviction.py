"""Crypto mined volume factor: volume-return conviction with range confirmation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, signed_power, ts_corr, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_conviction",
    "nickname": "量价确认",
    "theme": ["volume"],
    "formula_latex": (
        r"\operatorname{rank}\left("
        r"\operatorname{ts\_corr}_{20}\left(\frac{\Delta C}{C_{-1}}, V\right)^3"
        r"\cdot\operatorname{ts\_rank}_{20}\left(\frac{H-L}{C}\right)"
        r"\right)"
    ),
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 4,
    "min_warmup_bars": 45,
    "notes": "Positive volume/return correlation with a wide daily range confirms trend; negative correlation flags divergence.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    returns = safe_div(delta(close, 1), close.shift(1))
    daily_range = safe_div(high - low, close)
    corr = ts_corr(returns, volume, 20)
    range_rank = ts_rank(daily_range, 20)
    raw = signed_power(corr, 3) * range_rank
    return rank(raw)