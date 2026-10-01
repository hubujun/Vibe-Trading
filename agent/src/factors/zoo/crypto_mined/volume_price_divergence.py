"""crypto VOLUME: volume-price divergence captured by rolling correlation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_divergence",
    "nickname": "量价背离",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{decay}_5\\!\\left(\\mathrm{corr}_{20}(P_t, V_t)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": (
        "Rolling 20-bar Pearson correlation between close and volume, negated so "
        "that a persistent breakdown of the normal price-volume co-movement "
        "(price rising on falling volume, or vice versa) ranks high. Smoothed "
        "with a 5-bar linear decay to reduce noise."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated decayed price-volume correlation."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    corr = ts_corr(close, volume, 20)
    return decay_linear(-corr, 5)