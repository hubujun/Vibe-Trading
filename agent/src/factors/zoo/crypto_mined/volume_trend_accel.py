"""crypto VOLUME: volume trend acceleration."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_trend_accel",
    "nickname": "成交量趋势加速",
    "theme": ["volume"],
    "formula_latex": "\\frac{\\Delta_5 \\mathrm{MA}_5(V)}{\\mathrm{MA}_{20}(V)}",
    "columns_required": ["volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Short-term volume moving average change relative to long-term volume level.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume trend acceleration factor, aligned to volume."""
    volume = panel["volume"].astype(float)

    vol_short = ts_mean(volume, 5)
    vol_long = ts_mean(volume, 20)
    factor = safe_div(delta(vol_short, 5), vol_long)

    return factor