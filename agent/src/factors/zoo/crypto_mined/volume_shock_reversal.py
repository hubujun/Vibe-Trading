"""crypto VOLUME: volume shock reversal with recent price move."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_shock_reversal",
    "nickname": "成交量冲击反转",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{ts\\_rank}\\left(\\frac{V_t}{\\mathrm{MA}_{20}(V)} - 1, 20\\right) \\cdot \\frac{P_t - P_{t-5}}{P_{t-5}}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "High volume shocks combined with recent negative returns are expected to reverse.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-shock reversal score aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    vol_base = ts_mean(volume, 20)
    vol_shock = safe_div(volume, vol_base) - 1.0
    shock_rank = ts_rank(vol_shock, 20)

    delta_5 = delta(close, 5)
    ret_5 = safe_div(delta_5, close - delta_5)

    return -1.0 * shock_rank * ret_5