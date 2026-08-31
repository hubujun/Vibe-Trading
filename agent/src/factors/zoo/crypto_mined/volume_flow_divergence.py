"""crypto_mined_volume_flow_divergence: short/long volume MA vs price MA."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_divergence",
    "nickname": "Volume Flow Divergence",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\mathrm{ts\\_rank}_{20}\\left(\\frac{MA_5(V)-MA_{40}(V)}{MA_{40}(V)+\\epsilon} - \\frac{MA_5(P)-MA_{40}(P)}{MA_{40}(P)+\\epsilon}\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 60,
    "notes": "Measures whether volume is accelerating faster than price over the same lookback horizon.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    v_fast = ts_mean(volume, 5)
    v_slow = ts_mean(volume, 40)
    p_fast = ts_mean(close, 5)
    p_slow = ts_mean(close, 40)

    v_trend = safe_div(v_fast - v_slow, v_slow + 1e-12)
    p_trend = safe_div(p_fast - p_slow, p_slow + 1e-12)
    divergence = v_trend - p_trend

    return rank(ts_rank(divergence, 20))