"""crypto reversal: wick exhaustion reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, zscore

__alpha_meta__ = {
    "id": "crypto_mined_reversal_wick_exhaustion",
    "nickname": "Wick Exhaustion Reversal",
    "theme": ["reversal"],
    "formula_latex": "-\\mathrm{rank}(\\mathrm{decay\\_linear}_3(\\mathrm{rank}((C-L)/(H-L)) \\cdot \\mathrm{rank}(\\mathrm{zscore}(V)), 3))",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1h"],
    "decay_horizon": 3,
    "min_warmup_bars": 4,
    "notes": "Fades closes near the high of the bar when volume is above average.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the wick exhaustion reversal factor aligned to close."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_position = safe_div(close - low, high - low)
    volume_surge = zscore(volume)
    exhaustion = rank(range_position) * rank(volume_surge)

    return -rank(decay_linear(exhaustion, 3))