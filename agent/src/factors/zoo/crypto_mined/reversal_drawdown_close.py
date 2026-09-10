"""Crypto mined reversal: recent drawdown absorbed by closing wick on unusual volume."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_reversal_drawdown_close",
    "nickname": "超跌反弹强度",
    "theme": ["reversal"],
    "formula_latex": "\\mathrm{zscore}\\left(-\\frac{\\Delta_5 C}{C_{-5}}\\cdot\\frac{C-L}{H-L}\\cdot\\mathrm{ts\\_rank}(V,20)\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 21,
    "notes": "Cross-sectional reversal after a 5-day drawdown when the coin closes near the high on high trailing volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    ret5 = safe_div(delta(close, 5), close.shift(5))
    close_location = safe_div(close - low, high - low)
    volume_rank = ts_rank(volume, 20)

    reversal_signal = (-ret5) * close_location * volume_rank

    return zscore(reversal_signal)