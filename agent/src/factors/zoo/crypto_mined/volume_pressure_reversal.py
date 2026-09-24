"""crypto MINED: volume-pressure reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_pressure_reversal",
    "nickname": "量压反转",
    "theme": ["reversal", "volume"],
    "formula_latex": "-\\mathrm{rank}(r_t) \\cdot \\mathrm{rank}\\left(\\frac{V_t}{\\mathrm{ts\\_mean}(V,20)_t}\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 23,
    "notes": "Fades short-term returns that occur on unusually high volume; ranked cross-sectionally and smoothed.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-pressure reversal factor."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    vol_base = ts_mean(volume, 20)
    vol_ratio = safe_div(volume, vol_base)

    pressure = -rank(ret) * rank(vol_ratio)
    return decay_linear(pressure, 3)