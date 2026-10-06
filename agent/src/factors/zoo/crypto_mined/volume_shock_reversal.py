"""crypto VOLUME: volume-shock price reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_shock_reversal",
    "nickname": "量能冲击反转",
    "theme": ["volume"],
    "formula_latex": "-z\\big(\\mathrm{ts\\_rank}_{10}(V) \\cdot R_5\\big)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 8,
    "min_warmup_bars": 12,
    "notes": "Cross-sectional z-score of the negative volume-rank-weighted 5-bar return. High-volume price runs are expected to mean-revert.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    vol_rank = ts_rank(volume, 10)
    ret5 = safe_div(close - close.shift(5), close.shift(5))
    return zscore(-vol_rank * ret5)