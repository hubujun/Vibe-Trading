"""crypto VOLUME: volume-surprise conditioned short-horizon reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_shock_reversal",
    "nickname": "放量冲击反转",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{rank}\\!\\left(\\frac{V_t}{\\overline{V}^{(20)}_t}-1\\right)\\cdot \\mathrm{rank}\\!\\left(r^{(5)}_t\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 4,
    "min_warmup_bars": 22,
    "notes": (
        "Cross-sectionally ranks the volume surprise (volume vs its 20-bar mean) "
        "and the trailing 5-bar return, then takes the negated product. Large "
        "volume spikes paired with strong recent gains are penalised (crowding "
        "exhaustion); spikes after declines are rewarded."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return negated product of ranked volume surprise and ranked 5-bar return."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    vol_mean = ts_mean(volume, 20)
    vol_surprise = safe_div(volume, vol_mean) - 1.0

    ret5 = safe_div(close - close.shift(5), close.shift(5))

    return -rank(vol_surprise) * rank(ret5)