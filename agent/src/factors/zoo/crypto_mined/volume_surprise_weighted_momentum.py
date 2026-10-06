"""crypto VOLUME: volume-surprise weighted momentum — returns that matter carry abnormal volume."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_surprise_weighted_momentum",
    "nickname": "量惊奇加权动量",
    "theme": ["volume", "momentum", "microstructure"],
    "formula_latex": (
        r"\frac{1}{10}\sum_{i=0}^{9}"
        r"\left(\frac{V_{t-1-i}-\overline{V}_{60}}{\sigma_{60}(V)}\right)"
        r"\cdot\frac{\Delta_{1}C_{t-i}}{C_{t-1-i}}"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 71,
    "notes": (
        "Lagged 60-bar volume z-score multiplies the next-bar simple return, "
        "and the product is averaged over 10 bars. Returns printed on abnormal "
        "volume are treated as more informative and weighted more heavily."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the 10-bar mean of volume-surprise weighted returns."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    vol_mean = ts_mean(volume, 60)
    vol_std = ts_std(volume, 60)
    vol_z = safe_div(volume - vol_mean, vol_std)

    ret1 = safe_div(delta(close, 1), close.shift(1))
    weighted = vol_z.shift(1) * ret1
    return ts_mean(weighted, 10)