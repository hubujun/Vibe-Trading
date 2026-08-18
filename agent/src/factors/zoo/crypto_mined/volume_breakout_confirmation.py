"""crypto_mined_volume_breakout_confirmation: volume-confirmed price breakout."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_breakout_confirmation",
    "nickname": "VolumeBreakoutConfirmation",
    "theme": ["volume"],
    "formula_latex": (
        r"\mathrm{rank}\left(\mathrm{ts\_mean}\left("
        r"\Delta_{5} C \cdot \frac{V}{\mathrm{ts\_mean}(V,10)}"
        r"\cdot\frac{H-L}{\mathrm{ts\_mean}(H-L,20)},10\right)\right)"
    ),
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 0,
    "min_warmup_bars": 45,
    "notes": "Recent return scaled by volume surge and range expansion, smoothed and ranked.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-confirmed breakout score with same shape as close."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    ret_5 = delta(close, 5)
    volume_surge = safe_div(volume, ts_mean(volume, 10))
    avg_range = ts_mean(high - low, 20)
    range_expansion = safe_div(high - low, avg_range)

    score = ret_5 * volume_surge * range_expansion
    smoothed = ts_mean(score, 10)
    return rank(smoothed)