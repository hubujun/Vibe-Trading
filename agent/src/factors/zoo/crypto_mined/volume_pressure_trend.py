"""crypto VOLUME: volume-confirmed directional pressure trend."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, signed_power, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_pressure_trend",
    "nickname": "成交量压力趋势",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{mean}\\left(\\mathrm{sign}(r_t)|r_t|^{0.5} \\frac{V_t}{\\mathrm{mean}(V,10)}, 10\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 25,
    "notes": "Signed volume-pressure: return direction scaled by relative volume intensity.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return smoothed signed volume-pressure trend."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret1 = safe_div(delta(close, 1), close.shift(1))
    rel_vol = safe_div(volume, ts_mean(volume, 10))
    pressure = signed_power(ret1, 0.5) * rel_vol
    return ts_mean(pressure, 10)