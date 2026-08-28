"""CRYPTO VOLUME: rolling volume-return / price-return correlation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_corr",
    "nickname": "量价收益相关性",
    "theme": ["volume"],
    "formula_latex": "\\text{corr}_{20}\\left(\\frac{V_t - V_{t-1}}{V_{t-1}},\\frac{P_t - P_{t-1}}{P_{t-1}}\\right)",
    "columns_required": ["volume", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 22,
    "notes": "Correlation between volume returns and close price returns over 20 bars.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return rolling correlation of volume returns with price returns."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    volume_return = safe_div(delta(volume, 1), volume.shift(1))
    price_return = safe_div(delta(close, 1), close.shift(1))

    return ts_corr(volume_return, price_return, 20)