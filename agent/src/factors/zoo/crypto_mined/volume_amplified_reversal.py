"""crypto_mined_volume_amplified_reversal: volume-weighted short-term reversal signal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_amplified_reversal",
    "nickname": "VolumeAmplifiedReversal",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\left(0.5-\\mathrm{ts\\_rank}_{10}(\\Delta C_t)\\right)\\cdot\\mathrm{rank}(V_t)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "Recently weak price action in relatively high-volume crypto assets receives a higher rank, capturing volume-amplified short-term reversal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = delta(close, 1)
    ret_rank = ts_rank(ret, 10)
    vol_rank = rank(volume)
    raw = (0.5 - ret_rank) * vol_rank
    return rank(raw)