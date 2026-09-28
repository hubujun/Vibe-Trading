"""crypto REVERSAL: volume-weighted stretch away from the local mean."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_reversal",
    "nickname": "量能加权均值回归",
    "theme": ["reversal"],
    "formula_latex": (
        "\\mathrm{zscore}\\!\\left("
        "-\\frac{C_t - \\bar{C}_{10}}{\\bar{C}_{10}}"
        "\\cdot \\mathrm{ts\\_rank}(V_t, 20)\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 21,
    "notes": (
        "Short-horizon displacement from the 10-bar mean is faded, but only "
        "in proportion to the rolling rank of volume: stretched prices on "
        "heavy volume are treated as exhaustion, while quiet drifts are "
        "ignored. Cross-sectionally z-scored."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Volume-scaled mean-reversion signal, cross-sectionally z-scored."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    fair = ts_mean(close, 10)
    dev = safe_div(close - fair, fair)
    vol_rank = ts_rank(volume, 20)
    stretched = -dev * vol_rank
    return zscore(stretched)