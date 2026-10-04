"""crypto VOLUME: volume-weighted short-term reversal."""

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_reversal",
    "nickname": "量加权反转",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{ts\\_mean}\\left(\\frac{\\Delta C_t}{C_{t-1}} \\cdot \\mathrm{ts\\_rank}(V,20), 5\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Negative 5-day mean of daily return multiplied by 20-day volume rank; captures volume-confirmed short-term reversal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-weighted reversal factor."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    d = delta(close, 1)
    ret = safe_div(d, close - d)
    vol_rank = ts_rank(volume, 20)
    raw = ret * vol_rank
    return -ts_mean(raw, 5)