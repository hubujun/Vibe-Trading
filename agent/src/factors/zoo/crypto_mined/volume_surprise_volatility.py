"""crypto_mined_volume_surprise_volatility: volume surprise scaled by range."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_std, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_surprise_volatility",
    "nickname": "Volume Surprise Volatility",
    "theme": ["volume", "volatility"],
    "formula_latex": r"z\left(\frac{V_t - V_{t-5}}{\mathrm{std}_{20}(V)}\cdot\frac{H-L}{C}\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 7,
    "min_warmup_bars": 20,
    "notes": "Unexpected volume expansion combined with wide price ranges identifies high-conviction volatility regimes.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    vol_surprise = safe_div(delta(volume, 5), ts_std(volume, 20))
    range_pct = safe_div(high - low, close)

    return zscore(vol_surprise * range_pct)