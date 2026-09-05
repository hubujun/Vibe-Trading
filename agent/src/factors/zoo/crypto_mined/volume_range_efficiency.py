"""Volume-range efficiency factor for crypto markets."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_efficiency",
    "nickname": "volume_range_efficiency",
    "theme": ["volume"],
    "formula_latex": r"R_{cs}\left(\frac{V_t/\mathrm{MA}_{20}(V_t)}{R_t/\mathrm{MA}_{20}(R_t)}\right),\quad R_t=(H_t-L_t)/O_t",
    "columns_required": ["open", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "Captures volume shocks that occur without proportional range expansion, indicating absorbed liquidity or stealth positioning.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    open_ = panel["open"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_ratio = safe_div(high - low, open_)
    volume_ratio = safe_div(volume, ts_mean(volume, 20))
    range_shock = safe_div(range_ratio, ts_mean(range_ratio, 20))
    volume_range_efficiency = safe_div(volume_ratio, range_shock)

    return rank(volume_range_efficiency).reindex_like(close)