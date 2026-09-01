"""crypto volume theme: trend confirmation via volume-price correlation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_trend_confirmation",
    "nickname": "VolumeTrendConfirm",
    "theme": ["volume"],
    "formula_latex": "rank(\\rho_t(V, C)) \\times rank(r_{20})",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Ranks the 20-period volume-close correlation and the 20-period return.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    corr = ts_corr(volume, close, 20)
    ret20 = safe_div(delta(close, 20), close.shift(20))
    factor = rank(corr) * rank(ret20)
    return decay_linear(factor, 5)