"""crypto VOLUME: volume-return correlation reversal."""

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr_reversal",
    "nickname": "量价相关反转",
    "theme": ["volume", "reversal"],
    "formula_latex": "-\\mathrm{ts\\_corr}(r_t, \\Delta V_t, 20)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "Negative rolling correlation between daily return and volume change. When volume rises with price, factor is negative; when volume rises against price, factor is positive.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret = safe_div(delta(close, 1), close.shift(1))
    vol_chg = delta(volume, 1)
    corr = ts_corr(ret, vol_chg, 20)
    factor = -1.0 * corr
    return factor