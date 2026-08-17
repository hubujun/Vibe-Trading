"""Volume spike reversal: high relative volume combined with recent price declines."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, signed_power, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_spike_reversal",
    "nickname": "Volume Spike Reversal",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{rank}\left( \mathrm{ts\_rank}\left( \left(\frac{V_t}{\mathrm{mean}_{20}(V)}\right)^2, 20\right) \cdot \mathrm{rank}(-\Delta P_t) \right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 39,
    "notes": "Targets short-term reversal after volume spikes: high relative volume plus a recent price drop ranks highest.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return cross-sectionally ranked volume-spike reversal score."""
    close = panel["close"]
    volume = panel["volume"]

    vol_ratio = safe_div(volume, ts_mean(volume, 20))
    vol_spike = signed_power(vol_ratio, 2)
    spike_rank = ts_rank(vol_spike, 20)

    recent_ret = delta(close, 1)
    reversal_rank = rank(-recent_ret)

    score = spike_rank * reversal_rank
    return rank(score)