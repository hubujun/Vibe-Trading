"""crypto MINED: volume-anchored short-term reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_anchored_reversal",
    "nickname": "VolumeAnchoredReversal",
    "theme": ["reversal", "volume"],
    "formula_latex": r"\mathrm{rank}\left((0.5-\mathrm{ts\_rank}(r_t,24))\cdot \mathrm{ts\_rank}(V_t,24)\right),\quad r_t=\frac{C_t-C_{t-1}}{C_{t-1}}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1h"],
    "decay_horizon": 3,
    "min_warmup_bars": 24,
    "notes": "Reversal signal amplified by volume. Recent relative losers with high volume rank receive higher values; recent relative winners with high volume rank receive lower values.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-adjusted short-term reversal rank."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    delta_close = delta(close, 1)
    ret = safe_div(delta_close, close - delta_close)
    ret_rank = ts_rank(ret, 24)
    volume_rank = ts_rank(volume, 24)
    score = (0.5 - ret_rank) * volume_rank

    return rank(score)