"""crypto_mined_volume_return_corr: volume/return rolling correlation rank."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr",
    "nickname": "VolRetCorr",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{rank}\left(\rho_{20}\left(\mathrm{volume}, \Delta \mathrm{close}\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 22,
    "notes": "Ranks assets by rolling correlation between trading volume and price change.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret = delta(close, 1)
    corr = ts_corr(volume, ret, 20)
    return rank(corr).reindex(index=close.index, columns=close.columns)