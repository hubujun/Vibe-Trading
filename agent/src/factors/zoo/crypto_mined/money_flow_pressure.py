"""crypto VOLUME: volume-weighted close-location pressure."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_money_flow_pressure",
    "nickname": "量价资金流压力",
    "theme": ["volume"],
    "formula_latex": (
        "\\mathrm{decay}_{10}\\!\\left(\\frac{V_t}{\\bar{V}_{20}} \\cdot "
        "\\frac{2C_t - H_t - L_t}{H_t - L_t}\\right)"
    ),
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 20,
    "notes": (
        "Close location value within the bar range, scaled by relative volume "
        "and linearly decayed over 10 bars. Positive when above-average "
        "participation pushes close near the bar high (accumulation pressure)."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the decayed, volume-scaled close location value."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    clv = safe_div(2.0 * close - high - low, high - low)
    vol_ratio = safe_div(volume, ts_mean(volume, 20))
    flow = clv * vol_ratio
    return decay_linear(flow, 10)