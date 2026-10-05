"""crypto LIQUIDITY: Amihud-style illiquidity, |return| per unit of traded volume."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, safe_div

__alpha_meta__ = {
    "id": "crypto_mined_amihud_illiq",
    "nickname": "Amihud非流动性",
    "theme": ["liquidity"],
    "formula_latex": "\\mathrm{Decay}_5\\!\\left(\\frac{|r_t|}{V_t}\\right),\\quad r_t=\\frac{C_t-C_{t-1}}{C_{t-1}}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 6,
    "notes": (
        "Amihud illiquidity: absolute log-free return divided by traded volume. "
        "High values mean the price moves a lot per unit of volume (thin book). "
        "Smoothed with a 5-bar linear decay to reduce single-bar noise."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the 5-bar decay-weighted Amihud illiquidity score."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    prev_close = close.shift(1)
    ret = safe_div(delta(close, 1), prev_close)
    abs_ret = ret.abs()

    illiq = safe_div(abs_ret, volume)
    return decay_linear(illiq, 5) * 1e6