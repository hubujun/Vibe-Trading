"""crypto volume theme: short-term reversal after volume spikes."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_spike_reversal",
    "nickname": "VolumeSpikeReversal",
    "theme": ["volume"],
    "formula_latex": "rank\\left(\\frac{V - \\mu_{20}(V)}{\\sigma_{20}(V)}\\right) \\times rank(-r_5)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "Ranks volume z-scores against the negative 5-day return.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret5 = safe_div(delta(close, 5), close.shift(5))
    vol_mean = ts_mean(volume, 20)
    vol_std = ts_std(volume, 20)
    vol_z = safe_div(volume - vol_mean, vol_std)
    signal = rank(vol_z) * rank(-1.0 * ret5)
    return signal