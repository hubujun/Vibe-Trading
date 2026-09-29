"""crypto LIQUIDITY: illiquidity shock reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, signed_power, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_illiq_shock_reversal",
    "nickname": "流动性冲击反转",
    "theme": ["liquidity", "reversal"],
    "formula_latex": "-\\mathrm{sign}(r_t) \\cdot \\frac{|r_t|/v_t - \\mathrm{mean}_{20}(|r|/v)}{\\mathrm{std}_{20}(|r|/v)}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "Amihud illiquidity z-score multiplied by negative return sign for reversion.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated sign-weighted illiquidity shock."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    prev = close.shift(1)
    r1 = safe_div(close - prev, prev)

    illiq = safe_div(r1.abs(), volume)
    mu = ts_mean(illiq, 20)
    sd = ts_std(illiq, 20)
    illiq_z = safe_div(illiq - mu, sd)

    return -illiq_z * signed_power(r1, 0)