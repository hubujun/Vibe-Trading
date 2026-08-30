"""Crypto mined 20-bar range location confirmed by rolling volume percentile."""

import pandas as pd

from src.factors.base import rank, safe_div, ts_max, ts_min, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_range_volume_breakout",
    "nickname": "RangeVolumeBreakout",
    "theme": ["momentum"],
    "formula_latex": "\\operatorname{rank}\\left( \\frac{C_t - \\operatorname{ts\\_min}_{20}(L)}{\\operatorname{ts\\_max}_{20}(H) - \\operatorname{ts\\_min}_{20}(L)} \\cdot \\operatorname{ts\\_rank}_{20}(V) \\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "High score when price is near the upper end of the 20-bar range and volume is also high relative to its own rolling distribution.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    upper = ts_max(high, 20)
    lower = ts_min(low, 20)
    rng = upper - lower

    price_loc = safe_div(close - lower, rng + 1e-12)
    volume_pct = ts_rank(volume, 20)

    score = price_loc * volume_pct
    return rank(score)