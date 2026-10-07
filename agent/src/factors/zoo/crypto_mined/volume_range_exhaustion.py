"""crypto volume: volume-range exhaustion factor."""

import pandas as pd

from src.factors.base import safe_div, ts_max, ts_min

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_exhaustion",
    "nickname": "量能区间衰竭",
    "theme": ["volume"],
    "formula_latex": r"- \frac{V_t}{\max_{30} V} \cdot \frac{C_t - \min_{30} L}{\max_{30} H - \min_{30} L}",
    "columns_required": ["volume", "close", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 30,
    "notes": "High relative volume near the top of the recent high-low range is faded as exhaustion.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-weighted range-position exhaustion factor."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float).reindex(index=close.index, columns=close.columns)
    low = panel["low"].astype(float).reindex(index=close.index, columns=close.columns)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    vol_rel = safe_div(volume, ts_max(volume, 30))
    hh = ts_max(high, 30)
    ll = ts_min(low, 30)
    range_pos = safe_div(close - ll, hh - ll)

    return -vol_rel * range_pos