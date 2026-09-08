"""Volatility factor: squeeze-state close-range breakout direction."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_max, ts_mean, ts_min, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volatility_range_exhaustion",
    "nickname": "RangeExhaustion",
    "theme": ["volatility"],
    "formula_latex": r"F_t=\mathrm{rank}\Biggl(\Biggl(\frac{C_t-\mathrm{ts\_min}(L,20)}{\mathrm{ts\_max}(H,20)-\mathrm{ts\_min}(L,20)}-\frac12\Biggr)\cdot\mathrm{ts\_rank}\Bigl(\bigl(\frac{\mathrm{ts\_max}(H,20)-\mathrm{ts\_min}(L,20)}{\mathrm{ts\_mean}(C,20)}\bigr)^{-1},40\Bigr)\Biggr)",
    "columns_required": ["close", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 60,
    "notes": "Squeeze state is the rolling rank of inverse normalised range; direction comes from close position inside the 20-day high-low band.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return range-exhaustion breakout score aligned to close."""
    close = panel["close"].astype(float)
    high = panel["high"].reindex_like(close).astype(float)
    low = panel["low"].reindex_like(close).astype(float)

    hh = ts_max(high, 20)
    ll = ts_min(low, 20)
    avg_close = ts_mean(close, 20)

    width = safe_div(hh - ll, avg_close)
    one = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    inv_width = safe_div(one, width)
    squeeze_state = ts_rank(inv_width, 40)

    pos = safe_div(close - ll, hh - ll) - 0.5

    return rank(pos * squeeze_state)