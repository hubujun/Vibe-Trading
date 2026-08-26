"""crypto_mined volume-return correlation breakout."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, safe_div, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_corr_breakout",
    "nickname": "Volume-Return Correlation Breakout",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{F} = \\mathrm{zscore}\\left(\\mathrm{decay}_{5}\\left(\\rho_{20}(\\mathrm{volume}, r_t)\\right)\\right),\\quad r_t = \\frac{C_t - C_{t-1}}{C_{t-1}}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Cross-sectional z-score of smoothed 20-bar correlation between transaction volume and close-to-close returns. Rising correlation indicates volume is confirming directional price moves.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    delta_close = delta(close, 1)
    prev_close = close - delta_close
    ret = safe_div(delta_close, prev_close)

    corr = ts_corr(volume, ret, 20)
    smoothed = decay_linear(corr, 5)
    return zscore(smoothed)