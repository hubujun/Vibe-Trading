"""crypto REVERSAL: decay-weighted overnight gap mean reversion."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div

__alpha_meta__ = {
    "id": "crypto_mined_gap_reversal",
    "nickname": "隔夜跳空反转",
    "theme": ["reversal"],
    "formula_latex": "-\\mathrm{DecayLin}_{5}\\!\\left(\\frac{O_t - C_{t-1}}{C_{t-1}}\\right)",
    "columns_required": ["open", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 6,
    "notes": "Negated decay-weighted overnight gaps; gap-ups tend to fade and gap-downs tend to bounce.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    open_ = panel["open"].astype(float)
    close = panel["close"].astype(float)
    prev_close = close.shift(1)
    gap = safe_div(open_ - prev_close, prev_close)
    return -decay_linear(gap, 5)