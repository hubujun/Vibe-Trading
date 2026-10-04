"""crypto VOLUME: displacement of price from its rolling volume-weighted average."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_price_gap",
    "nickname": "量权均价偏离",
    "theme": ["volume"],
    "formula_latex": (
        "-\\mathrm{zscore}\\left("
        "\\frac{P_t - \\mathrm{VWAP}_t}{\\mathrm{VWAP}_t}"
        "\\right),\\quad "
        "\\mathrm{VWAP}_t = \\frac{\\overline{P_t V_t}_{20}}{\\overline{V_t}_{20}}"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 22,
    "notes": (
        "Rolling turnover-weighted average price built from the ratio of the 20-bar mean "
        "of close*volume to the 20-bar mean of volume. The factor is the negated "
        "cross-sectional z-score of the relative gap between the last close and that "
        "anchor, i.e. it buys instruments trading below the level at which recent "
        "turnover actually changed hands and sells those extended above it."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated cross-sectional z-score of the volume-weighted price gap."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    vw_num = ts_mean(close * volume, 20)
    vw_den = ts_mean(volume, 20)
    vwap_proxy = safe_div(vw_num, vw_den)

    gap = safe_div(close - vwap_proxy, vwap_proxy)

    return -zscore(gap)