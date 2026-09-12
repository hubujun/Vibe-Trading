"""crypto VOLUME: short-term reversal amplified by volume dry-up."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_dryup_reversal",
    "nickname": "缩量反转",
    "theme": ["volume"],
    "formula_latex": "z\\left(-\\left(1-\\mathrm{rank}_{20}(V)\\right)\\cdot\\left(\\frac{P_t}{P_{t-5}}-1\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Contrarian 5-bar return weighted by the degree of volume dry-up; mean reversion is assumed stronger when recent volume sits low within its own 20-bar range.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the z-scored volume-dryup-weighted reversal signal."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(
        index=close.index, columns=close.columns
    )

    ret5 = safe_div(close, close.shift(5)) - 1.0
    vol_rank = ts_rank(volume, 20)
    dryup = 1.0 - vol_rank

    raw = -dryup * ret5
    return zscore(raw)