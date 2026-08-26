"""crypto mined volume-activated reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_activated_reversal",
    "nickname": "Volume Exhaustion Reversal",
    "theme": ["volume", "reversal"],
    "formula_latex": r"-rank\left(\frac{V_t}{\mathrm{MA}_{20}(V_t)} \cdot \left(\mathrm{ts\_rank}_{10}(C_t) - 0.5\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 20,
    "notes": "Recent price extremes with above-average volume are faded cross-sectionally.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return negative cross-sectional rank of volume-scaled recent price extremity."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    vol_surge = safe_div(volume, ts_mean(volume, 20))
    recent_extreme = ts_rank(close, 10) - 0.5
    return -rank(vol_surge * recent_extreme)