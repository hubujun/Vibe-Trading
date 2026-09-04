"""Crypto mined volume: moving-range breakout confirmed by a rolling volume multiplier."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, ts_max, ts_mean, ts_min

__alpha_meta__ = {
    "id": "crypto_mined_volume_surge_range_breakout",
    "nickname": "VolumeSurgeRangeBreakout",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{factor}=\mathrm{decay\_linear}_3\left(\mathrm{rank}\left(\frac{C-\mathrm{ts\_min}_{20}(L)}{\mathrm{ts\_max}_{20}(H)-\mathrm{ts\_min}_{20}(L)}\cdot\frac{V}{\mathrm{ts\_mean}_{20}(V)}\right)\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 23,
    "notes": "Combines the close location in the trailing 20-bar high-low range with a 20-bar volume multiplier, then smooths the cross-sectional rank.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    high = panel["high"]
    low = panel["low"]
    volume = panel["volume"]

    range_high = ts_max(high, 20)
    range_low = ts_min(low, 20)
    close_loc = safe_div(close - range_low, range_high - range_low)

    vol_mean = ts_mean(volume, 20)
    vol_mult = safe_div(volume, vol_mean)

    score = close_loc * vol_mult
    return decay_linear(rank(score), 3)