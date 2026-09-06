"""crypto SENTIMENT: volume-return interaction factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_interaction",
    "nickname": "VRI",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{VR}_{t} = \\frac{1}{10}\\sum_{i=0}^{9} Z\\left(\\mathrm{ts\\_rank}(V_{t-i},20)\\right) \\times Z\\left(\\mathrm{ts\\_rank}(r_{t-i},20)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 45,
    "notes": "Rolling cross-sectional alignment of volume rank and return rank. Positive values indicate volume-confirmed upward moves.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return a volume-return interaction score aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1) + 1e-12)
    vol_rank = ts_rank(volume, 20)
    ret_rank = ts_rank(ret, 20)

    interaction = zscore(vol_rank) * zscore(ret_rank)
    return ts_mean(interaction, 10)