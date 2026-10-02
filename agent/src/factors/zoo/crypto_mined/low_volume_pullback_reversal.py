"""crypto VOLUME: low-volume pullback reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_low_volume_pullback_reversal",
    "nickname": "缩量回调反转",
    "theme": ["volume"],
    "formula_latex": "-r^{(5)}_t \\cdot \\left(1 - \\mathrm{tsrank}_{60}(V_t)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 65,
    "notes": (
        "Negated 5-bar return scaled by the complement of the 60-bar time-series "
        "rank of volume. Declines that occur on unusually thin participation "
        "(low volume rank) score highest, capturing supply-dry pullbacks that "
        "tend to mean-revert; heavy-volume declines are damped."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return low-volume pullback reversal score aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret5 = safe_div(close - close.shift(5), close.shift(5))
    vol_rank = ts_rank(volume, 60)

    factor = -ret5 * (1.0 - vol_rank)
    return factor.replace([float("inf"), float("-inf")], float("nan"))