"""crypto VOLUME: close versus a rolling volume-weighted average price."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_vol_weighted_price_gap",
    "nickname": "量价均价偏离",
    "theme": ["volume"],
    "formula_latex": (
        "-z\\left(\\frac{C_t - \\mathrm{VWAP}^{(20)}_t}"
        "{\\mathrm{VWAP}^{(20)}_t}\\right),\\quad "
        "\\mathrm{VWAP}^{(20)}_t = "
        "\\frac{\\overline{V_t \\cdot TP_t}^{(20)}}{\\overline{V_t}^{(20)}},\\quad "
        "TP_t = \\frac{H_t + L_t + C_t}{3}"
    ),
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 21,
    "notes": (
        "Volume-weighted fair-value gap: close relative to a 20-bar VWAP proxy "
        "built from the typical price and traded volume. The negated "
        "cross-sectional z-score fades rich prices (close far above the "
        "volume-weighted fair value) and buys discounts."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated z-score of close's deviation from the rolling VWAP proxy."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    typical = (high + low + close) / 3.0
    vwap_proxy = safe_div(ts_mean(volume * typical, 20), ts_mean(volume, 20))
    gap = safe_div(close - vwap_proxy, vwap_proxy)

    return -zscore(gap)