"""crypto MINED microstructure: volume-range alignment."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_microstructure_volume_range_alig",
    "nickname": "Volume-Range Alignment",
    "theme": ["microstructure"],
    "formula_latex": "\\rho_{20}\\left(\\mathrm{vol}_t, \\frac{C_t-L_t}{H_t-L_t}\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 20,
    "notes": "Rolling correlation between traded volume and the close position inside the daily range. High values show volume is being absorbed near strong intraday closing levels.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the rolling volume/range-location correlation."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_loc = safe_div(close - low, high - low)
    return ts_corr(volume, range_loc, 20)