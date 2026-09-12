"""crypto VOLUME: rolling correlation between returns and volume changes."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_corr_div",
    "nickname": "量价相关性背离",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{corr}_{20}\\!\\left(\\Delta p_t,\\ \\Delta v_t\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "Negative 20-bar rolling correlation between price returns and "
             "volume deltas. When volume stops confirming price moves the raw "
             "correlation falls and the diverging move is expected to revert.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negated return/volume-change correlation."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret = delta(close, 1)
    vol_chg = delta(volume, 1)
    corr = ts_corr(ret, vol_chg, 20)
    return -corr