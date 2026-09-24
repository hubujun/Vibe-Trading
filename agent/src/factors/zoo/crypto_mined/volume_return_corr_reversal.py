"""crypto VOLUME: volume-return correlation reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr_reversal",
    "nickname": "量价相关反转",
    "theme": ["volume", "microstructure"],
    "formula_latex": "-\\mathrm{rank}\\left(\\mathrm{ts\\_corr}(\\Delta P_t, V_t, n)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 20,
    "notes": "Reversal signal from the rolling correlation between daily returns and volume; high positive correlation indicates volume-driven price moves that may revert.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    n = 20
    ret = delta(close, 1)
    corr = ts_corr(ret, volume, n)
    return -rank(corr)