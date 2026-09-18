"""crypto MOMENTUM: intraday return momentum confirmed by volume trend."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_intraday_momentum_volume_trend",
    "nickname": "日内动量量能趋势",
    "theme": ["momentum", "volume"],
    "formula_latex": "\\operatorname{rank}\\left(\\frac{\\operatorname{ts\\_mean}(C/O-1,5)}{\\operatorname{ts\\_std}(C/O-1,5)}\\right) \\times \\operatorname{rank}\\left(\\frac{\\operatorname{ts\\_mean}(V,5)}{\\operatorname{ts\\_mean}(V,20)}-1\\right)",
    "columns_required": ["close", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 21,
    "notes": "Risk-adjusted intraday momentum multiplied by medium-term volume expansion.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    open_ = panel["open"].astype(float)
    volume = panel["volume"].astype(float)

    intraday_ret = safe_div(close, open_, eps=1e-12) - 1.0
    mom = safe_div(ts_mean(intraday_ret, 5), ts_std(intraday_ret, 5), eps=1e-12)
    vol_trend = safe_div(ts_mean(volume, 5), ts_mean(volume, 20), eps=1e-12) - 1.0

    return rank(mom) * rank(vol_trend)