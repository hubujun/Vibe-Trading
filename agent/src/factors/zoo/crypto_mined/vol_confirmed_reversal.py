"""crypto REVERSAL: volume-confirmed short-horizon mean reversion."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.factors.base import safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_vol_confirmed_reversal",
    "nickname": "量能确认短期反转",
    "theme": ["reversal", "volume"],
    "formula_latex": (
        "-\\,z\\left(\\frac{C_t}{\\mathrm{MA}_5(C_t)}-1\\right)"
        "\\cdot\\left(0.25+\\mathrm{tsrank}_{20}(V_t)\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": (
        "Short-horizon dislocation of price from its own 5-bar mean is "
        "cross-sectionally z-scored and negated (mean reversion). The "
        "reversal bet is then scaled up by the rolling 20-bar percentile of "
        "volume, so dislocations on abnormally heavy turnover are traded "
        "harder than quiet drifts. Volume rank is shifted to [0.25, 1.25] so "
        "that the directional sign of the signal is never flipped."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-confirmed reversal score, aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    stretch = safe_div(close, ts_mean(close, 5)) - 1.0
    reversal = -zscore(stretch)
    vol_rank = ts_rank(volume, 20)

    factor = reversal * (0.25 + vol_rank)
    return factor.replace([np.inf, -np.inf], np.nan)