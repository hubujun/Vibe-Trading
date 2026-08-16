"""Volatility-volume synchronisation factor for crypto."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, delta, ts_std, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volatility_volume_sync",
    "nickname": "波动-量能同步",
    "theme": ["volatility"],
    "formula_latex": "\\text{Factor}_t = zscore\\left(\\mathrm{ts\\_corr}_{20}\\left(\\sigma_{20}\\left(\\frac{\\Delta C_t}{C_{t-1}}\\right), \\frac{\\Delta V_t}{V_{t-1}}\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 42,
    "notes": "Ranks the rolling correlation between realised volatility and volume changes; high values imply volume-confirmed volatility moves.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    rv = ts_std(ret, 20)
    vol_chg = safe_div(delta(volume, 1), volume.shift(1))

    sync = ts_corr(rv, vol_chg, 20)
    return zscore(sync)