"""crypto VOLUME: volume-return asymmetry from signed squared returns."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, signed_power, ts_cov

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_asymmetry",
    "nickname": "Volume Return Asymmetry",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{rank}\left(\mathrm{Cov}_{20}\left(V_t, \mathrm{signed\_power}\left(\frac{P_t-P_{t-1}}{P_{t-1}}, 2\right)\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 30,
    "notes": "Positive values indicate volume concentrates on up-moves; negative on down-moves.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return decayed cross-sectional rank of volume/return asymmetry."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    ret = safe_div(delta(close, 1), close - delta(close, 1))
    signed_sq_ret = signed_power(ret, 2)
    asym = ts_cov(volume, signed_sq_ret, 20)
    return decay_linear(rank(asym), 5)