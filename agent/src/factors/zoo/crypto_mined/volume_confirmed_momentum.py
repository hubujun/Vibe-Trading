"""crypto momentum: volume-confirmed trend."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_confirmed_momentum",
    "nickname": "VolumeConfirmedMomentum",
    "theme": ["momentum"],
    "formula_latex": "zscore(\\mathrm{ts\\_rank}(r_{20},10)) \\cdot zscore(\\rho(\\mathrm{volume}, \\Delta \\mathrm{close}, 10))",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 30,
    "notes": "20-day return rank combined with rolling volume-price correlation.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret20 = safe_div(delta(close, 20), close.shift(20))
    vol_corr = ts_corr(volume, delta(close, 1), 10)

    momentum = zscore(ts_rank(ret20, 10))
    confirmation = zscore(vol_corr)
    factor = momentum * confirmation
    return zscore(factor)