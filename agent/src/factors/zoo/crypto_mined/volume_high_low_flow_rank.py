"""Crypto mined volume factor: intraday high-low range location flow."""
from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_high_low_flow_rank",
    "nickname": "RangeFlowRank",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{rank}\left(\frac{DWMA_{10}\left(V_t(\operatorname{loc}(C_t)-\operatorname{loc}(O_t))\right)}{\operatorname{std}_{20}\left(V_t(\operatorname{loc}(C_t)-\operatorname{loc}(O_t))\right)}\right)",
    "columns_required": ["close", "high", "low", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 40,
    "notes": "Ranks assets by volume-weighted movement of the close location inside the daily high-low range, normalized by rolling flow volatility.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    open_ = panel["open"].astype(float)
    volume = panel["volume"].astype(float)

    close_location = safe_div(close - low, high - low)
    open_location = safe_div(open_ - low, high - low)

    signed_flow = volume * (close_location - open_location)
    smoothed_flow = decay_linear(signed_flow, 10)
    flow_vol = ts_std(signed_flow, 20)
    normalized_flow = safe_div(smoothed_flow, flow_vol)

    return rank(normalized_flow)