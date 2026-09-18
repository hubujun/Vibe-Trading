"""crypto VOLUME: net signed volume pressure over recent bars."""

from __future__ import annotations

import pandas as pd

from src.factors.base import zscore, delta, ts_mean, safe_div, signed_power

__alpha_meta__ = {
    "id": "crypto_mined_signed_volume_pressure",
    "nickname": "带符号成交量压力",
    "theme": ["volume"],
    "formula_latex": (
        "z\\left(\\mathrm{mean}_{10}\\left(\\mathrm{sgn}(\\Delta P_t)"
        "\\cdot \\frac{V_t}{\\mathrm{mean}_{20}(V_t)}\\right)\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 30,
    "notes": (
        "Signs each bar's relative volume by the direction of the price "
        "change, then averages over 10 bars to obtain a net signed flow "
        "pressure. Persistent positive values signal sustained buy-side "
        "participation on above-average volume."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = delta(close, 1)
    ret_sign = signed_power(ret, 0.0)

    vol_base = ts_mean(volume, 20)
    vol_ratio = safe_div(volume, vol_base)

    signed_flow = ret_sign * vol_ratio
    pressure = ts_mean(signed_flow, 10)

    return zscore(pressure)