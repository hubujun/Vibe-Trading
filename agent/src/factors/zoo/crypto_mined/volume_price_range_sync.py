"""crypto VOLUME: volume synchronisation with daily close location."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_range_sync",
    "nickname": "Volume Close-Location Sync",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{zscore}\left(\rho_{20}\left(V_t, \frac{\mathrm{close}_t-\mathrm{low}_t}{\mathrm{high}_t-\mathrm{low}_t}\right)\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 0,
    "min_warmup_bars": 20,
    "notes": "Volume expanding when close is near the intra-period high is interpreted as buying pressure.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return cross-sectional z-score of volume vs close-location correlation."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float).reindex(index=close.index, columns=close.columns)
    low = panel["low"].astype(float).reindex(index=close.index, columns=close.columns)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    close_loc = safe_div(close - low, high - low)
    sync = ts_corr(volume, close_loc, 20)
    return zscore(sync)