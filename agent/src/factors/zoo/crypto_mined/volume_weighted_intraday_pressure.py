"""crypto VOLUME: volume-weighted intraday close location pressure."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_intraday_pressure",
    "nickname": "量加权日内压力",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{decay\_linear}\left(\mathrm{zscore}(V_t) \cdot \frac{C_t-O_t}{H_t-L_t}, 5\right)",
    "columns_required": ["close", "high", "low", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 5,
    "notes": "Volume-weighted close location within the daily range, decayed; positive when high volume accompanies closes near highs.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the decayed volume-weighted intraday pressure."""
    close = panel["close"].astype(float)
    open_ = panel["open"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)
    pressure = safe_div(close - open_, high - low + 1e-12)
    signal = zscore(volume) * pressure
    return decay_linear(signal, 5)