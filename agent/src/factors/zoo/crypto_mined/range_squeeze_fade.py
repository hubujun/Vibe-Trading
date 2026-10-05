"""crypto VOLATILITY: range-compression mean reversion."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_max, ts_min, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_range_squeeze_fade",
    "nickname": "缩量区间反转",
    "theme": ["volatility", "reversal"],
    "formula_latex": (
        "-\\left(\\frac{C_t-\\min_{20}L}{\\max_{20}H-\\min_{20}L}-0.5\\right)"
        "\\cdot\\left(1-\\mathrm{ts\\_rank}_{20}(R_t)\\right)"
    ),
    "columns_required": ["close", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 0,
    "min_warmup_bars": 20,
    "notes": (
        "Position of close inside the 20-bar high/low channel, faded, and "
        "weighted by range compression (1 - rank of relative bar range). "
        "Reversion is trusted most when the channel is unusually tight; "
        "high-volatility trending regimes are down-weighted."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the range-compression reversion score."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)

    bar_range = safe_div(high - low, close)
    squeeze = 1.0 - ts_rank(bar_range, 20)

    hi = ts_max(high, 20)
    lo = ts_min(low, 20)
    channel_pos = safe_div(close - lo, hi - lo) - 0.5

    return -(channel_pos) * squeeze