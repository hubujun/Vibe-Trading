"""crypto VOLUME/REVERSAL: volume-shock reversal.

Large one-bar moves that arrive on abnormally heavy volume are more likely
to be liquidity-driven overshoots than persistent repricing, so the signal
takes the negative of the return, weighted by how unusual the volume was.
"""

from __future__ import annotations

import pandas as pd

from src.factors.base import (
    decay_linear,
    delta,
    safe_div,
    ts_mean,
    ts_rank,
)

__alpha_meta__ = {
    "id": "crypto_mined_volume_shock_reversal",
    "nickname": "放量反转",
    "theme": ["volume", "reversal"],
    "formula_latex": (
        "-\\mathrm{decay}_5\\Big(r_t \\cdot "
        "\\big(\\mathrm{tsrank}_{20}(V_t/\\bar{V}_{20}) + 0.5\\big)\\Big),"
        "\\quad r_t = \\frac{C_t - C_{t-1}}{C_{t-1}}"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": (
        "Short-horizon reversal weighted by a volume shock. The volume ratio "
        "V_t / mean(V,20) is converted to a within-instrument time-series rank "
        "in [0,1] so the weight is bounded; the signal is then smoothed with a "
        "linear decay. Heavy-volume moves pull the factor more negative."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-weighted short-horizon reversal score."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    prev_close = close.shift(1)
    ret = safe_div(delta(close, 1), prev_close)

    vol_baseline = ts_mean(volume, 20)
    vol_ratio = safe_div(volume, vol_baseline)

    # Bounded [0, 1] weight: how unusual today's volume is for this instrument.
    shock_weight = ts_rank(vol_ratio, 20) + 0.5

    signal = -ret * shock_weight
    return decay_linear(signal, 5)