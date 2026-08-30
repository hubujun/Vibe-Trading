"""crypto VOLUME: recency-weighted volume acceleration."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_accel_decay",
    "nickname": "VolumeAccelDecay",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\frac{\\mathrm{decay}(\\Delta_1 \\mathrm{vol}, 10)}{\\mathrm{mean}_{20}(\\mathrm{vol})}\\right) \\times \\mathrm{ts\\_rank}(\\mathrm{vol}, 20)",
    "columns_required": ["volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 30,
    "notes": "Recent volume acceleration normalized by average volume, with a volume percentile tilt.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return rank of decayed volume changes times rolling volume rank."""
    close = panel["close"]
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)
    vol_change = delta(volume, 1)
    dec_change = decay_linear(vol_change, 10)
    avg_vol = ts_mean(volume, 20)
    raw = safe_div(dec_change, avg_vol)
    return rank(raw) * ts_rank(volume, 20)