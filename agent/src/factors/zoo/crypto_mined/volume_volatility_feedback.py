"""Volume-confirmed volatility regime: rolling volatility rank amplified by volume surge."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, signed_power, ts_mean, ts_rank, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_volatility_feedback",
    "nickname": "VolumeVolatilityFeedback",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{rank}\left( \mathrm{ts\_rank}_{20}\left( \sigma_{10}\left( \frac{\Delta C_t}{C_{t-1}} \right) \right) \cdot \sqrt{ \frac{V_t}{\mathrm{ts\_mean}_{20}(V_t)} } \right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 30,
    "notes": "Captures volume-confirmed volatility states by combining rolling volatility rank with current volume relative to its 20-bar mean. NaN is propagated.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    realized_vol = ts_std(ret, 10)
    vol_rank = ts_rank(realized_vol, 20)

    volume_baseline = ts_mean(volume, 20)
    volume_surge = safe_div(volume, volume_baseline)
    compressed_surge = signed_power(volume_surge, 0.5)

    feedback = vol_rank * compressed_surge
    return rank(feedback)