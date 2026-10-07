"""crypto VOLUME: volume dry-up reversal signal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_vol_dryup_reversal",
    "nickname": "缩量反转",
    "theme": ["volume"],
    "formula_latex": "-\\,r_5 \\cdot \\left(1 - \\mathrm{rank}_t(\\bar{V}_{20})\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 25,
    "notes": (
        "Negative 5-bar return weighted by a low volume rank. Designed to "
        "capture exhaustion moves: price drift down while participation "
        "dries up, which historically mean-reverts upward."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume dry-up reversal score."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret5 = safe_div(delta(close, 5), close.shift(5))
    vol_slow = ts_mean(volume, 20)
    vol_rank = ts_rank(vol_slow, 20)

    return -ret5 * (1.0 - vol_rank)