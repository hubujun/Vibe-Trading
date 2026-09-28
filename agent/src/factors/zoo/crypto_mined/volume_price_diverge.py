"""crypto VOLUME: price--volume trend divergence reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_diverge",
    "nickname": "量价背离反转",
    "theme": ["volume"],
    "formula_latex": "-z\\!\\left(\\mathrm{rank}_{20}\\!\\left(\\bar r_5\\right) - \\mathrm{rank}_{20}\\!\\left(\\overline{\\Delta\\log V}_5\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": (
        "Measures the divergence between the ranked 5-day price drift and the "
        "ranked 5-day volume change. Large positive divergence (price up, volume "
        "down) is treated as exhaustion and assigned a negative score."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Negative of the ranked price-vs-volume trend gap."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = close.pct_change()
    dv = safe_div(volume.diff(), volume.shift(1))

    p_trend = ts_mean(ret, 5)
    v_trend = ts_mean(dv, 5)

    p_rank = ts_rank(p_trend, 20)
    v_rank = ts_rank(v_trend, 20)

    return -zscore(p_rank - v_rank)