"""crypto_mined_volume_price_coherence_momentum."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_corr, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_coherence_momentum",
    "nickname": "VolPriceCoherenceMom",
    "theme": ["momentum"],
    "formula_latex": "\\text{signal} = z(\\text{rank}(\\rho_{10}) \\times \\text{rank}(\\bar{r}_{10})),\\quad r=\\Delta C/C_{-1},\\ \\rho_{10}=\\text{ts\\_corr}(r, \\Delta V/V_{-1}, 10)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 10,
    "notes": "Momentum weighted by rolling cross-sectional correlation of returns to volume changes.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    prev_close = close - delta(close, 1)
    prev_volume = volume - delta(volume, 1)

    ret1 = safe_div(delta(close, 1), prev_close)
    vol_chg1 = safe_div(delta(volume, 1), prev_volume)

    coherence = ts_corr(ret1, vol_chg1, 10)
    ret_ma = ts_mean(ret1, 10)

    return zscore(rank(coherence) * rank(ret_ma))