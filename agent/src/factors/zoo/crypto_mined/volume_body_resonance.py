"""Crypto volume-body resonance: volume-scaled signed candle body, decayed and normalized cross-sectionally."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_body_resonance",
    "nickname": "Volume-Body Resonance",
    "theme": ["volume"],
    "formula_latex": r"Z\left(\mathrm{decay\_linear}_{10}\left(\frac{V_t}{\mathrm{mean}_{20}(V_t)} \cdot \frac{C_t-O_t}{H_t-L_t}\right)\right)",
    "columns_required": ["close", "high", "low", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 30,
    "notes": "Positive values indicate high-volume upward close pressure relative to the cross-section.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    open_ = panel["open"].astype(float)
    volume = panel["volume"].astype(float)

    range_ = high - low
    body = close - open_
    body_ratio = safe_div(body, range_)

    volume_ratio = safe_div(volume, ts_mean(volume, 20))
    resonance = body_ratio * volume_ratio
    smoothed = decay_linear(resonance, 10)

    return zscore(smoothed)