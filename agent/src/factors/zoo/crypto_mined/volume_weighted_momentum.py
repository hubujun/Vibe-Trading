"""crypto MOMENTUM: volume-weighted short-horizon momentum."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, ts_std, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_momentum",
    "nickname": "成交量加权动量",
    "theme": ["momentum", "volume"],
    "formula_latex": "z\\left(\\frac{\\mathrm{mean}_{10}(r_t \\cdot V_t/\\bar{V}_{20})}{\\mathrm{std}_{20}(r_t)}\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 30,
    "notes": (
        "Daily returns scaled by relative volume (volume / 20-bar mean volume), "
        "averaged over 10 bars and normalised by trailing 20-bar return volatility, "
        "then cross-sectionally z-scored."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return z-scored volume-weighted momentum, aligned to close index."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = close.pct_change()
    vol_rel = safe_div(volume, ts_mean(volume, 20))
    weighted = ret * vol_rel
    mom = ts_mean(weighted, 10)
    vol = ts_std(ret, 20)
    normalized = safe_div(mom, vol)
    return zscore(normalized)