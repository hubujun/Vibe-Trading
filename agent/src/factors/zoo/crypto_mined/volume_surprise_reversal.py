"""crypto_mined_volume_surprise_reversal: high-volume price extremes tend to revert."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, signed_power, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_surprise_reversal",
    "nickname": "VolumeSurpriseReversal",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{rank}\\left(\\mathrm{decay}_{5}\\left(\\mathrm{sign}(r_1)|r_1|^3 \\cdot \\mathrm{ts\\_rank}_{20}\\left(\\frac{V_t}{\\mathrm{mean}_{20}(V)}\\right)\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 50,
    "notes": "Momentum extremes accompanied by volume surges are treated as short-term reversal candidates.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    chg = delta(close, 1)
    ret_1 = safe_div(chg, close - chg)
    volume_surprise = safe_div(volume, ts_mean(volume, 20))
    volume_rank = ts_rank(volume_surprise, 20)

    extreme_move = signed_power(ret_1, 3)
    raw_reversal = -extreme_move * volume_rank
    return rank(decay_linear(raw_reversal, 5))