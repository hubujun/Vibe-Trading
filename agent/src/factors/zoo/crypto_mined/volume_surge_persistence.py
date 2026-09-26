"""crypto VOLUME: persistence of volume-change shocks (autocorrelation)."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_surge_persistence",
    "nickname": "成交量冲击持续性",
    "theme": ["volume"],
    "formula_latex": "z\\left(\\mathrm{corr}_{20}\\left(\\Delta\\log V_t,\\; \\Delta\\log V_{t-1}\\right)\\right)",
    "columns_required": ["volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 23,
    "notes": "Lag-1 autocorrelation of relative volume change. Positive = directional volume shocks that persist (informed flow / trending liquidity); negative = choppy one-off spikes.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return z-scored lag-1 autocorrelation of relative volume changes."""
    close = panel["close"]
    volume = panel["volume"].astype(float)

    vol_chg = safe_div(delta(volume, 1), volume.shift(1) + 1e-12)
    lagged = vol_chg.shift(1)

    persistence = ts_corr(vol_chg, lagged, 20)
    persistence = persistence.reindex(index=close.index, columns=close.columns)
    return zscore(persistence)