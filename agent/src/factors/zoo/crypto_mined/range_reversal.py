"""crypto REVERSAL/VOLATILITY: high-low range reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_range_reversal",
    "nickname": "区间反转",
    "theme": ["reversal", "volatility"],
    "formula_latex": "-\\text{ts\\_rank}_{20}(\\frac{high-low}{close}) \\cdot \\Delta close",
    "columns_required": ["close", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 21,
    "notes": "Reversal factor: negative of 20-day range rank times daily close return.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    hl_range = safe_div(high - low, close)
    range_rank = ts_rank(hl_range, 20)
    ret = delta(close, 1)
    return -range_rank * ret