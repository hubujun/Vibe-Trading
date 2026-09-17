"""crypto VOLUME: volume dry-up inside a compressed range (coiled spring)."""

from __future__ import annotations

import pandas as pd

from src.factors.base import (
    delta,
    safe_div,
    ts_max,
    ts_mean,
    ts_min,
    ts_std,
    zscore,
)

__alpha_meta__ = {
    "id": "crypto_mined_volume_dryup_coil",
    "nickname": "缩量蓄势突破",
    "theme": ["volume"],
    "formula_latex": (
        "z\\left(\\left(1-\\frac{V_{5}}{V_{20}}\\right)"
        "\\left(1-\\frac{\\sigma_{5}(r)}{\\sigma_{20}(r)}\\right)"
        "\\left(\\frac{C_t-\\min_{20}(L)}{\\max_{20}(H)-\\min_{20}(L)}-\\frac{1}{2}\\right)\\right)"
    ),
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 22,
    "notes": (
        "Coiled-spring factor: volume dry-up (short vs long average volume) "
        "times short-horizon volatility compression, signed by the close's "
        "position inside the 20-bar high/low range. Drying volume plus "
        "compressed realised vol near a range extreme is treated as a "
        "pending breakout in the direction of that extreme."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the z-scored coil intensity signed by range position."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))

    rng_hi = ts_max(high, 20)
    rng_lo = ts_min(low, 20)
    pos = safe_div(close - rng_lo, rng_hi - rng_lo)

    dryup = 1.0 - safe_div(ts_mean(volume, 5), ts_mean(volume, 20))
    compression = 1.0 - safe_div(ts_std(ret, 5), ts_std(ret, 20))

    coil = dryup * compression
    raw = coil * (pos - 0.5)

    return zscore(raw)