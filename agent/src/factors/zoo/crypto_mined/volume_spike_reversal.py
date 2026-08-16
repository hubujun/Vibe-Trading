"""crypto_mined_volume_spike_reversal factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_spike_reversal",
    "nickname": "VolumeSpikeReversal",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{rank}\\left(\\frac{V_t}{\\mathrm{SMA}_{20}(V_t)}\\right) \\cdot (\\mathrm{rank}(\\Delta C_t / C_t) - 0.5)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "Contrarian response to volume spikes: high volume strengthens the sign of the contemporaneous return in the opposite direction.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-spike reversal factor aligned to close index."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close)
    vol_spike = safe_div(volume, ts_mean(volume, 20))

    return -rank(vol_spike) * (rank(ret) - 0.5)