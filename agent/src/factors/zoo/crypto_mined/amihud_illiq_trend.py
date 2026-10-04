"""crypto LIQUIDITY: Amihud-style illiquidity regime trend."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_amihud_illiq_trend",
    "nickname": "Amihud非流动性趋势",
    "theme": ["liquidity"],
    "formula_latex": "\\frac{\\overline{ILLIQ}_{5} - \\overline{ILLIQ}_{20}}{\\overline{ILLIQ}_{20}},\\quad ILLIQ_t=\\frac{|\\Delta C_t|}{V_t}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 22,
    "notes": (
        "Per-bar Amihud illiquidity |dC| / V. The factor is the relative gap between "
        "the 5-bar and 20-bar means: positive values flag coins whose price impact per "
        "unit volume is deteriorating (thin/liquidity-stressed), negative values flag "
        "liquidity improvement."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the Amihud illiquidity regime trend, aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    abs_ret = delta(close, 1).abs()
    illiq = safe_div(abs_ret, volume)

    short = ts_mean(illiq, 5)
    long = ts_mean(illiq, 20)

    return safe_div(short - long, long)