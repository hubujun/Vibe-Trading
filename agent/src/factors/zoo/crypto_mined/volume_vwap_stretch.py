"""crypto VOLUME: rolling volume-weighted price stretch (VWAP deviation).

Build a rolling volume-weighted average of the typical price and measure how
far the last close sits above/below it. A close stretched far above the
volume-weighted fair level has been bought on heavy flow and is expected to
mean-revert; the deviation is smoothed to damp one-off prints.
"""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_vwap_stretch",
    "nickname": "量价偏离反转",
    "theme": ["volume"],
    "formula_latex": (
        "\\mathrm{rank}\\left(-\\frac{C_t - \\mathrm{VWAP}_t}{\\mathrm{VWAP}_t}\\right),"
        "\\quad \\mathrm{VWAP}_t = \\frac{\\mathrm{ts\\_mean}(V_i T_i, 20)}"
        "{\\mathrm{ts\\_mean}(V_i, 20)},\\; T_i = \\frac{H_i + L_i + C_i}{3}"
    ),
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 6,
    "min_warmup_bars": 22,
    "notes": (
        "Smoothed percentage deviation of close from a 20-bar volume-weighted "
        "typical price, signed for mean reversion."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negative VWAP-stretch score aligned to the close panel."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    typical = (high + low + close) / 3.0
    num = ts_mean(typical * volume, 20)
    den = ts_mean(volume, 20)
    vwap = safe_div(num, den)

    stretch = safe_div(close - vwap, vwap)
    smoothed = ts_mean(stretch, 3)

    return rank(-smoothed)