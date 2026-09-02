"""crypto VOLUME: rolling volume-return congruence, cross-sectionally ranked."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, ts_corr, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_correlation",
    "nickname": "VolumeReturnCorr",
    "theme": ["volume"],
    "formula_latex": "\\text{decay}_5\\left(\\text{rank}\\left(\\rho_{20}\\left(\\frac{\\Delta V_t}{V_{t-1}}, \\frac{\\Delta C_t}{C_{t-1}}\\right)\\right) \\times \\text{tsrank}_{20}(V_t)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "High rolling correlation between volume changes and price changes, amplified by volume rank, signals persistent volume-backed moves.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    n = 20
    close_ret = safe_div(delta(close, 1), close.shift(1))
    volume_ret = safe_div(delta(volume, 1), volume.shift(1))
    corr = ts_corr(volume_ret, close_ret, n)
    factor = decay_linear(rank(corr) * ts_rank(volume, n), 5)

    return factor.reindex(index=close.index, columns=close.columns)