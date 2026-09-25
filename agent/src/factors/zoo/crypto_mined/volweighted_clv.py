"""crypto MICROSTRUCTURE: volume-weighted close location value."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volweighted_clv",
    "nickname": "量价收盘位置",
    "theme": ["microstructure"],
    "formula_latex": (
        "z\\left(\\mathrm{decay}_{10}\\left("
        "\\frac{(C_t - L_t) - (H_t - C_t)}{H_t - L_t}"
        "\\cdot \\frac{V_t}{\\bar{V}_{20}}\\right)\\right)"
    ),
    "columns_required": ["high", "low", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 20,
    "notes": (
        "Close location value (CLV) in [-1, 1] measures where the bar closed "
        "inside its range; it is weighted by relative volume so that heavily "
        "traded bars dominate the accumulation/distribution read. A 10-bar "
        "linear decay smooths the signal and a cross-sectional z-score makes it "
        "comparable across instruments."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the smoothed, volume-weighted close location value."""
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    hl_range = high - low
    clv = safe_div((close - low) - (high - close), hl_range)

    rel_volume = safe_div(volume, ts_mean(volume, 20))
    weighted = clv * rel_volume
    smoothed = decay_linear(weighted, 10)

    return zscore(smoothed)