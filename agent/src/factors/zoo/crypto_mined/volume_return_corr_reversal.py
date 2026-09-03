"""Crypto mined volume: volume/return correlation reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr_reversal",
    "nickname": "量价相关反转",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{rank}\left(-\operatorname{ts\_corr}\left(\frac{P_t-P_{t-1}}{P_{t-1}}, \frac{V_t-V_{t-1}}{V_{t-1}}, 20\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 21,
    "notes": "Ranks assets by the negative 20-bar rolling correlation between price changes and volume changes.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return a cross-sectional rank of negative volume/return correlation."""
    close = panel["close"].astype(float)
    volume = panel["volume"].reindex_like(close).astype(float)

    price_chg = safe_div(delta(close, 1), close.shift(1))
    volume_chg = safe_div(delta(volume, 1), volume.shift(1))
    volume_return_corr = ts_corr(price_chg, volume_chg, 20)

    return rank(-volume_return_corr)