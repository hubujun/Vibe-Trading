"""crypto VOLUME: volume burst conditioned reversal signal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_burst_reversal",
    "nickname": "放量反转",
    "theme": ["volume"],
    "formula_latex": (
        "-\\frac{\\mathrm{close}_t - \\mathrm{close}_{t-5}}{\\mathrm{close}_{t-5}}"
        "\\cdot \\frac{\\mathrm{volume}_t}{\\mathrm{mean}(\\mathrm{volume},20)}"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": (
        "Negated 5-bar return scaled by a volume burst ratio (current volume over "
        "its 20-bar mean). Large moves accompanied by abnormal volume are assumed "
        "to be exhaustion-driven and mean-revert, so the sign of momentum is flipped."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-scaled reversal score, aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    momentum_5 = safe_div(delta(close, 5), close.shift(5))
    vol_base = ts_mean(volume, 20)
    vol_burst = safe_div(volume, vol_base)

    signal = -1.0 * momentum_5 * vol_burst
    return signal.reindex_like(close)