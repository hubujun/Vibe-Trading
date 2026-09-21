"""crypto MOMENTUM/VOLUME: close location confirmed by price-volume correlation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_confirmed_close_location",
    "nickname": "量价确认收盘位置动量",
    "theme": ["momentum", "volume", "microstructure"],
    "formula_latex": r"\mathrm{zscore}\left(\mathrm{ts\_mean}\left(\frac{\mathrm{delta}(close,5)}{close} \cdot \mathrm{ts\_corr}(close, volume, 10) \cdot \frac{close - low}{high - low}, 3\right)\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "5-day momentum confirmed by rolling price-volume correlation and close location within the daily range.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    mom = safe_div(delta(close, 5), close)
    vol_confirm = ts_corr(close, volume, 10)
    location = safe_div(close - low, high - low)
    raw = mom * vol_confirm * location
    return zscore(ts_mean(raw, 3))