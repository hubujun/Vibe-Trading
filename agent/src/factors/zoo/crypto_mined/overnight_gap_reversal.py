"""crypto MICROSTRUCTURE: overnight gap pressure versus its own volatility."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_overnight_gap_reversal",
    "nickname": "隔夜跳空反转",
    "theme": ["microstructure", "reversal"],
    "formula_latex": (
        "-\\mathrm{rank}\\left("
        "\\frac{\\mathrm{ts\\_mean}(g_t,3)}{\\mathrm{ts\\_std}(g_t,20)}"
        "\\right),\\quad g_t=\\frac{O_t-C_{t-1}}{C_{t-1}}"
    ),
    "columns_required": ["open", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": (
        "Overnight gap scaled by its 20-bar volatility and smoothed over 3 bars. "
        "The negative sign expresses gap fade: persistent one-sided gaps are "
        "expected to mean-revert over the following days."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated cross-sectional rank of the gap pressure ratio."""
    open_ = panel["open"].astype(float)
    close = panel["close"].astype(float)

    prev_close = close.shift(1)
    gap = safe_div(open_ - prev_close, prev_close)

    gap_pressure = safe_div(ts_mean(gap, 3), ts_std(gap, 20))
    return -rank(gap_pressure)