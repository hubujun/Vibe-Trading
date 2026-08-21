"""crypto_mined_volume_return_correlation: rolling correlation between returns and volume changes."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_correlation",
    "nickname": "Volume Return Correlation",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{VRC} = \mathrm{zscore}\left( \mathrm{ts\_corr}\left(\frac{\Delta C}{C_{t-1}}, \Delta V, n\right) \right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 4,
    "min_warmup_bars": 22,
    "notes": "Cross-sectional z-score of each instrument's rolling correlation between returns and volume changes. NaN propagated.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].reindex(index=close.index, columns=close.columns).astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    vol_chg = delta(volume, 1)

    corr = ts_corr(ret, vol_chg, 20)
    return zscore(corr)