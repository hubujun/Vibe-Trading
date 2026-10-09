"""crypto reversal: liquidity-weighted short-term reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_liquidity_weighted_reversal",
    "nickname": "流动性加权反转",
    "theme": ["reversal", "liquidity"],
    "formula_latex": "-z\\left(\\frac{C_t - C_{t-3}}{C_{t-3}}\\right) \\cdot \\mathrm{rank}\\!\\left(\\overline{C \\cdot V}_{5}\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 4,
    "min_warmup_bars": 6,
    "notes": "Fades 3-bar price moves with amplitude scaled by the cross-sectional rank of 5-bar average dollar volume; strong money-flow names mean-revert faster.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return negative 3-bar return scaled by ranked 5-bar dollar volume."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret_3 = safe_div(delta(close, 3), close.shift(3))
    dollar_vol = close * volume
    liq = ts_mean(dollar_vol, 5)
    return -zscore(ret_3) * rank(liq)