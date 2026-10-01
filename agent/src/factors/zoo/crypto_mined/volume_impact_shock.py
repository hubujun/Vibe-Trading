"""crypto VOLUME: Amihud-style volume price-impact shock."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_impact_shock",
    "nickname": "单位成交量冲击",
    "theme": ["volume"],
    "formula_latex": (
        "z\\left(\\mathrm{ts\\_rank}\\left("
        "\\mathrm{decay\\_linear}\\left("
        "\\frac{r_t^{2}}{V_t}, 10\\right), 60\\right)\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 71,
    "notes": (
        "Squared return per unit of raw volume (a volume-normalised price-impact / "
        "illiquidity proxy), linearly decay-weighted over 10 bars and ranked against "
        "its own 60-bar history so the level is relative to each instrument's own "
        "liquidity regime. The time-series rank is then cross-sectionally z-scored; "
        "high values indicate recent price movement achieved on unusually little "
        "volume, i.e. an impact shock / fragile book."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Cross-sectional z-score of the volume-adjusted price-impact shock."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    prev_close = close.shift(1)
    ret = safe_div(close - prev_close, prev_close)

    impact = safe_div(ret * ret, volume)
    smoothed = decay_linear(impact, 10)
    return zscore(ts_rank(smoothed, 60))