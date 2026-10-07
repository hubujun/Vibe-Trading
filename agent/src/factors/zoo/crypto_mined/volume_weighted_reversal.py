"""crypto volume: volume-weighted short-term reversal."""

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_reversal",
    "nickname": "量加权短期反转",
    "theme": ["volume"],
    "formula_latex": r"- \frac{\operatorname{ts\_mean}(r \cdot \operatorname{ts\_rank}(V,20), 5)}{\operatorname{ts\_mean}(\operatorname{ts\_rank}(V,20), 5)}",
    "columns_required": ["volume", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 24,
    "notes": "Volume-weighted average 1-bar return over 5 bars, negated for short-term reversal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-weighted short-term reversal factor."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    ret = safe_div(delta(close, 1), close.shift(1))
    vol_rank = ts_rank(volume, 20)
    weighted_ret = ret * vol_rank

    numerator = ts_mean(weighted_ret, 5)
    denominator = ts_mean(vol_rank, 5)

    return -safe_div(numerator, denominator)