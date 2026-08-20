"""Crypto volume-price trend divergence factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_trend_divergence",
    "nickname": "量价趋势背离",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}(V_5/V_{20}) - \\mathrm{rank}(P_5/P_{20})",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "Cross-sectional divergence between short/long volume mean and short/long close mean.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-price trend divergence factor aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    v_ratio = safe_div(ts_mean(volume, 5), ts_mean(volume, 20))
    p_ratio = safe_div(ts_mean(close, 5), ts_mean(close, 20))

    return rank(v_ratio) - rank(p_ratio)