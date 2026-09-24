"""crypto VOLUME: rolling correlation between daily returns and volume."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr",
    "nickname": "量价滚动相关",
    "theme": ["volume", "momentum"],
    "formula_latex": "\\mathrm{Corr}_{10}(r_t, V_t),\\quad r_t = \\frac{C_t - C_{t-1}}{C_{t-1}}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 12,
    "notes": "Positive values indicate volume-confirmed directional moves; negative values indicate volume on reversal days.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret = safe_div(delta(close, 1), close.shift(1))
    corr = ts_corr(ret, volume, 10)
    return corr