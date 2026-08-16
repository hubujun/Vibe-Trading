"""Crypto mined liquidity factor: volume-signed intraday close location."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_std, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_liquidity_imbalance",
    "nickname": "量价流动性失衡",
    "theme": ["liquidity"],
    "formula_latex": r"zscore\left(\frac{\mathrm{decay\_linear}_{10}((\frac{C-L}{H-L}-0.5)V)}{\mathrm{ts\_std}_{20}((\frac{C-L}{H-L}-0.5)V)}\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "Close location relative to intraday range weighted by volume; positive values indicate persistent one-sided flow.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    close_loc = safe_div(close - low, high - low)
    flow = (close_loc - 0.5) * volume
    smoothed_flow = decay_linear(flow, 10)
    flow_vol = ts_std(flow, 20)

    normalized_flow = safe_div(smoothed_flow, flow_vol)
    return zscore(normalized_flow)