"""crypto VOLUME: volume-confirmed moving-average trend, decayed and ranked."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_confirmed_trend",
    "nickname": "volume_confirmed_trend",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{rank}_t\left(\operatorname{decay}_5\left(\left(\frac{\mathrm{MA}_5(C_t)}{\mathrm{MA}_{20}(C_t)} - 1\right) \cdot \frac{V_t}{\mathrm{MA}_{20}(V_t)}\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 24,
    "notes": "A short/long moving-average trend is amplified by current volume relative to its 20-bar mean, linearly decayed over 5 bars, then cross-sectionally ranked. NaN is propagated through all rolling steps."
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the cross-sectional rank of volume-confirmed trend."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    fast_ma = ts_mean(close, 5)
    slow_ma = ts_mean(close, 20)
    trend = safe_div(fast_ma - slow_ma, slow_ma)

    vol_ref = ts_mean(volume, 20)
    vol_expansion = safe_div(volume, vol_ref)

    raw_composite = trend * vol_expansion
    smoothed_composite = decay_linear(raw_composite, 5)

    return rank(smoothed_composite)