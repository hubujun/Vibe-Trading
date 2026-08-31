"""crypto VOLUME: volume-expansion-confirmed short-term reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_expansion_reversal",
    "nickname": "Volume Expansion Reversal",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{rank}\left(-r_t \cdot \operatorname{ts\_rank}_{10}\left(\frac{V_t}{\operatorname{mean}_{20}(V_t)}\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 32,
    "notes": "Short-term reversal strengthened by an expanding volume percentile; high values occur after large down moves on high relative volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return cross-sectionally ranked volume-confirmed reversal signal."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    short_ret = safe_div(close.diff(), close.shift(1))
    average_volume = ts_mean(volume, 20)
    volume_expansion = safe_div(volume, average_volume)
    expansion_rank = ts_rank(volume_expansion, 10)

    return rank(-short_ret * expansion_rank)