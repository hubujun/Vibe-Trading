"""crypto VOLUME: short-horizon reversal conditioned on a volume shock."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_shock_reversal",
    "nickname": "放量反转",
    "theme": ["volume"],
    "formula_latex": (
        "z\\left(-\\left(\\frac{C_t}{\\mathrm{mean}_{5}(C_t)} - 1\\right) "
        "\\cdot \\mathrm{tsrank}_{20}\\!\\left(\\frac{V_t}{\\mathrm{mean}_{20}(V_t)}\\right)\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 40,
    "notes": (
        "Negated 5-bar price displacement multiplied by the time-series rank of the "
        "current volume-to-20-bar-average-volume ratio. Recent decliners that print "
        "an unusually large volume burst score highest (capitulation reversal), "
        "while quiet drifts get a near-zero score because the shock rank is small."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-shock-conditioned short-horizon reversal score."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    displacement = safe_div(close, ts_mean(close, 5)) - 1.0
    volume_ratio = safe_div(volume, ts_mean(volume, 20))
    shock = ts_rank(volume_ratio, 20)

    raw = -1.0 * displacement * shock
    return zscore(raw)