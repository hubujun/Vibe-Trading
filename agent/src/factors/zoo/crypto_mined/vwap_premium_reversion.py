"""crypto VOLUME: volume-weighted VWAP premium reversion."""

from __future__ import annotations

import pandas as pd

from src.factors.base import (
    rank,
    safe_div,
    ts_mean,
    ts_std,
)

__alpha_meta__ = {
    "id": "crypto_mined_vwap_premium_reversion",
    "nickname": "量价乖离回归",
    "theme": ["volume"],
    "formula_latex": (
        r"-\left(0.5+\mathrm{rank}(V_t)\right)\cdot"
        r"\frac{(C_t-\mathrm{VWAP}_{20})/C_t}"
        r"{\sigma_{60}\left((C-\mathrm{VWAP}_{20})/C\right)}"
    ),
    "columns_required": ["high", "low", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 80,
    "notes": (
        "Volume-weighted VWAP premium reversion: the typical price "
        "(high+low+close)/3 is averaged with volume weights over 20 bars to form "
        "a traded-price anchor. The relative gap between close and that anchor is "
        "standardized by its own 60-bar dispersion and signed negatively "
        "(premium -> expected reversion), then amplified when current volume is "
        "cross-sectionally elevated, since heavy participation at a stretched "
        "price is the strongest reversion signal."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-weighted VWAP premium reversion factor."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    typical = (high + low + close) / 3.0
    vwap20 = safe_div(
        ts_mean(typical * volume, 20),
        ts_mean(volume, 20),
    )

    gap = safe_div(close - vwap20, close)
    gap_z = safe_div(gap, ts_std(gap, 60))

    volume_weight = 0.5 + rank(volume)
    return -gap_z * volume_weight