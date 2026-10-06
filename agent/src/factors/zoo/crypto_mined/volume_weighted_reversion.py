"""crypto VOLUME: volume-weighted price deviation reversion."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_reversion",
    "nickname": "量价偏离反转",
    "theme": ["volume"],
    "formula_latex": "-z\\!\\left(\\frac{C_t}{\\mathrm{VWAP}^{(5)}_t}-1\\right)\\cdot \\mathrm{ts\\_rank}_{20}(V_t)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 6,
    "min_warmup_bars": 22,
    "notes": (
        "Constructs a 5-bar volume-weighted average price, measures the "
        "cross-sectional z-score of the close's deviation from it, and gates the "
        "reversion signal by the 20-bar time-series rank of volume. Deviations "
        "on heavy volume are treated as exhaustion and mean-reverted."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-gated reversion of close versus a 5-bar VWAP proxy."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    vwap = safe_div(ts_mean(close * volume, 5), ts_mean(volume, 5))
    deviation = safe_div(close, vwap) - 1.0

    vol_rank = ts_rank(volume, 20)

    return -zscore(deviation) * vol_rank