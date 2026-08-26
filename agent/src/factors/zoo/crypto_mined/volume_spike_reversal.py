"""crypto_mined_volume_spike_reversal: volume-spike short-term reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_spike_reversal",
    "nickname": "volume_spike_reversal",
    "theme": ["volume"],
    "formula_latex": r"-\mathrm{rank}\left(\frac{V_t}{\mathrm{mean}_{20}(V)}\right)\cdot\mathrm{rank}\left(\frac{P_t-P_{t-1}}{P_{t-1}}\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 21,
    "notes": "Negative of volume-spike rank times one-bar return rank; fades high-volume directional bursts.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return negative rank product used as a volume-spike reversal signal."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close - delta(close, 1))
    vol_spike = safe_div(volume, ts_mean(volume, 20))

    return -1.0 * rank(vol_spike) * rank(ret)