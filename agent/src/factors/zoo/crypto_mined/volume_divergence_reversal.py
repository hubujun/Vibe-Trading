"""Crypto volume divergence reversal factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_divergence_reversal",
    "nickname": "Volume Divergence Reversal",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{rank}\\left(\\frac{V-\\mathrm{ts\\_mean}(V,20)}{\\mathrm{ts\\_std}(V,20)}\\right)\\cdot\\mathrm{rank}\\left(\\mathrm{ts\\_mean}(r,5)\\right),\\quad r_t=\\frac{P_t-P_{t-1}}{P_{t-1}}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 20,
    "notes": "Volume spikes combined with short-term price moves are used as a reversal signal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    vol_ma = ts_mean(volume, 20)
    vol_sd = ts_std(volume, 20)
    vol_z = safe_div(volume - vol_ma, vol_sd + 1e-12, eps=1e-12)

    returns = safe_div(delta(close, 1), close.shift(1) + 1e-12, eps=1e-12)
    ret_5 = ts_mean(returns, 5)

    return rank(vol_z) * rank(ret_5) * -1.0