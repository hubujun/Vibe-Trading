"""crypto VOLUME: signed-volume pressure imbalance (contrarian)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_pressure_reversal",
    "nickname": "成交量方向压力反转",
    "theme": ["volume"],
    "formula_latex": (
        "-\\mathrm{rank}\\!\\left("
        "\\frac{\\overline{\\mathrm{sgn}(\\Delta c_t)\\,V_t}}"
        "{\\overline{V_t}}\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": (
        "Rolling 20-bar imbalance between up-bar and down-bar volume, "
        "normalised by total volume. Strong one-sided volume pressure is "
        "treated as crowding and faded cross-sectionally."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated cross-sectional rank of directional volume pressure."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    direction = np.sign(delta(close, 1))
    signed_volume = volume * direction

    imbalance = safe_div(ts_mean(signed_volume, 20), ts_mean(volume, 20))
    return -rank(imbalance)