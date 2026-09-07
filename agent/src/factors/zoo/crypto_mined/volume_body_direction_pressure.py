"""Volume-confirmed intraday body direction pressure."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_body_direction_pressure",
    "nickname": "量能方向",
    "theme": ["volume"],
    "formula_latex": r"zscore(decay_linear(\frac{C_t-O_t}{H_t-L_t} \cdot \frac{V_t}{\bar{V}_{20}}, 10))",
    "columns_required": ["close", "high", "low", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 30,
    "notes": "Close-to-open body direction scaled by above-normal volume, then decay-linearly smoothed.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-weighted intraday direction pressure aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)
    open_price = panel["open"].astype(float).reindex(index=close.index, columns=close.columns)
    high = panel["high"].astype(float).reindex(index=close.index, columns=close.columns)
    low = panel["low"].astype(float).reindex(index=close.index, columns=close.columns)

    body_direction = safe_div(close - open_price, high - low)
    vol_ratio = safe_div(volume, ts_mean(volume, 20))
    signed_vol = body_direction * vol_ratio

    return zscore(decay_linear(signed_vol, 10))