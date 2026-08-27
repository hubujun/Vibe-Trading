"""Crypto volume flow imbalance: net close-location buying pressure scaled by relative volume."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_imbalance",
    "nickname": "VolumeFlowImbalance",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{zscore}\\left( \\mathrm{decay\\_linear}\\left( \\left( \\frac{C-L}{H-L} - \\frac{H-C}{H-L} \\right) \\cdot \\frac{V}{\\mathrm{ts\\_mean}(V,20)}, 20 \\right) \\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 40,
    "notes": "Close-location net buying pressure from daily candles, weighted by volume relative to its 20-bar mean, linearly decayed and cross-sectionally standardized.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-flow imbalance factor aligned to panel['close']."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    rng = high - low
    buy = safe_div(close - low, rng)
    sell = safe_div(high - close, rng)
    net_buy = buy - sell

    vol_ratio = safe_div(volume, ts_mean(volume, 20))
    raw = net_buy * vol_ratio
    return zscore(decay_linear(raw, 20))