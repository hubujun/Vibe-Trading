"""Crypto volume-price correlation confirmation factor.

Captures whether recent return-volume correlation is strengthening
while volume remains elevated relative to its trailing average.
"""
from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, safe_div, signed_power, ts_corr, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_corr_imbalance",
    "nickname": "Volume Price Correlation Imbalance",
    "theme": ["volume"],
    "formula_latex": r"zscore(decay_linear(signed_power(ts_corr(\Delta close / close_{t-1}, \Delta volume / volume_{t-1}, 10), 3) * volume / ts_mean(volume, 20), 5))",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Volume-price confirmation; propagates NaN instead of zero-filling.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    returns = safe_div(delta(close, 1), close.shift(1))
    volume_returns = safe_div(delta(volume, 1), volume.shift(1))
    vol_corr = ts_corr(returns, volume_returns, 10)
    vol_ratio = safe_div(volume, ts_mean(volume, 20))
    interaction = signed_power(vol_corr, 3) * vol_ratio
    factor = zscore(decay_linear(interaction, 5))

    return factor.reindex(index=close.index, columns=close.columns)