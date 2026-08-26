"""crypto VOLUME: volume-confirmed short-term reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_confirmed_reversal",
    "nickname": "量能反转",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(-\\mathrm{ts\\_rank}(V_t,20)\\cdot\\frac{P_t-P_{t-1}}{P_{t-1}}\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 20,
    "notes": "Mean reversion after high-volume price moves; high volume down moves receive positive rank.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return cross-sectional rank of volume-scaled negative return."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    vol_rank = ts_rank(volume, 20)
    signal = -vol_rank * ret

    return rank(signal)