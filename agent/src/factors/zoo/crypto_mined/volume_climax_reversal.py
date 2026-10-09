"""crypto VOLUME: volume-climax reversal after stretched short-horizon moves."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.factors.base import delta, safe_div, signed_power, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_climax_reversal",
    "nickname": "放量冲高反转",
    "theme": ["volume"],
    "formula_latex": (
        "\\mathrm{zscore}\\!\\left[-\\mathrm{sgn}(r_{5})\\,|r_{5}|^{1/2}\\;"
        "\\mathrm{ts\\_rank}(V,20)\\right],\\quad r_{5}=\\frac{C_t-C_{t-5}}{C_{t-5}}"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": (
        "Climax-reversal proxy: a 5-bar price move is weighted by how extreme "
        "current turnover is versus its own 20-bar history. Moves printed on "
        "top-decile volume are treated as exhaustive and faded; signs are "
        "inverted so high factor values = expected reversal bounce."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-weighted short-horizon reversal score."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    # 5-bar return, outlier-compressed by signed square root
    ret5 = safe_div(delta(close, 5), close.shift(5))
    stretched = signed_power(ret5, 0.5)

    # how climactic is today's turnover within the recent window
    vol_rank = ts_rank(volume, 20)

    raw = -stretched * vol_rank
    out = zscore(raw)
    return out.where(np.isfinite(out))