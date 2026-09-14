"""crypto VOLUME: volume-price divergence via rolling correlation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_vol_price_corr",
    "nickname": "量价背离相关性",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{corr}_{20}\\!\\left(\\frac{\\Delta P_t}{P_{t-1}},\\ \\frac{\\Delta V_t}{V_{t-1}}\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 22,
    "notes": "Rolling 20-bar correlation between relative price change and relative volume change, negated. Positive price/volume co-movement is already priced, so weak or negative correlation flags volume-price divergence that tends to mean-revert.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated 20-bar price-change / volume-change correlation."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    vol_chg = safe_div(delta(volume, 1), volume.shift(1))

    corr = ts_corr(ret, vol_chg, 20)
    return -1.0 * corr