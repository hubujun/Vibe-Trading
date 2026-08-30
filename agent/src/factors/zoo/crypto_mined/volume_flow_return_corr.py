"""Crypto volume-flow / price-return correlation persistence factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_return_corr",
    "nickname": "VolumeFlowReturnCorr",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}(\\mathrm{decay}_{5}(\\rho_{20}(\\Delta V/V, \\Delta C/C)))",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Cross-sectional rank of the persistence of the rolling correlation between volume-flow and price returns.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    d_close = delta(close, 1)
    d_volume = delta(volume, 1)

    close_ret = safe_div(d_close, close - d_close)
    volume_flow = safe_div(d_volume, volume - d_volume)

    corr = ts_corr(close_ret, volume_flow, 20)

    return rank(decay_linear(corr, 5))