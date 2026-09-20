"""crypto VOLUME: volume-weighted intraday price pressure."""

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_intraday_pressure",
    "nickname": "量权日内压力",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\frac{\\mathrm{ts\\_mean}\\left(\\frac{\\mathrm{close}-\\mathrm{open}}{\\mathrm{high}-\\mathrm{low}} \\times \\mathrm{volume}, 10\\right)}{\\mathrm{ts\\_mean}(\\mathrm{volume}, 10)}\\right)",
    "columns_required": ["close", "open", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 10,
    "notes": "Volume-weighted average of the close position within the daily high-low range. Positive values indicate buying pressure near highs.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return rank of volume-weighted intraday price pressure."""
    close = panel["close"].astype(float)
    open_ = panel["open"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)
    intraday_pos = safe_div(close - open_, high - low)
    weighted = intraday_pos * volume
    num = ts_mean(weighted, 10)
    den = ts_mean(volume, 10)
    vw_pos = safe_div(num, den)
    return rank(vw_pos)