"""crypto VOLUME: short-term volume-rank reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_rank_reversal",
    "nickname": "Short-Term Volume Rank Reversal",
    "theme": ["volume"],
    "formula_latex": "-\\left(\\operatorname{Rank}_{5}(V_t) - 0.5\\right) \\cdot r_t",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 6,
    "notes": "Short-term price reversal conditioned on whether volume is high or low relative to its 5-bar history.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return short-term reversal conditioned on 5-bar volume rank."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret = safe_div(delta(close, 1), close.shift(1))
    vol_rank = ts_rank(volume, 5)
    return -(vol_rank - 0.5) * ret