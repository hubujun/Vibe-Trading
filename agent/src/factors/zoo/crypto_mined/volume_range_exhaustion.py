"""crypto_mined_volume_range_exhaustion: volume range extremes fade return extremes."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_max, ts_min, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_exhaustion",
    "nickname": "Volume Range Exhaustion",
    "theme": ["volume"],
    "formula_latex": "\\operatorname{rank}\\left(\\operatorname{rank}\\left(\\frac{V - \\operatorname{ts\\_min}(V,20)}{\\operatorname{ts\\_max}(V,20) - \\operatorname{ts\\_min}(V,20) + \\epsilon}\\right) \\times \\left(0.5 - \\operatorname{ts\\_rank}(\\Delta C_1,20)\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 40,
    "notes": "Fades recent return extremes when volume is at an extreme of its own 20-bar range.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    v_max = ts_max(volume, 20)
    v_min = ts_min(volume, 20)
    v_range_pos = safe_div(volume - v_min, v_max - v_min + 1e-12)

    vol_rank = rank(v_range_pos)
    ret_rank = ts_rank(delta(close, 1), 20)

    return rank(vol_rank * (0.5 - ret_rank))