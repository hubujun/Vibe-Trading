"""crypto VOLUME: volume dry-up reversal.

Combines a slow percentile of relative volume with the cross-sectional
percentile of the 20-bar return. Names that have both drained their volume
AND sold off receive the highest score, betting on a mean-reverting bounce
once selling pressure exhausts; the additive form keeps the two legs from
cancelling when both are extreme on the same side.
"""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_dryup_reversal",
    "nickname": "缩量反转",
    "theme": ["volume"],
    "formula_latex": (
        "z\\left(\\left(0.5-\\mathrm{ts\\_rank}_{60}"
        "\\left(\\frac{V_t}{\\overline{V}_{30}}\\right)\\right)"
        "+\\left(0.5-\\mathrm{rank}(r_{20})\\right)\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 90,
    "notes": (
        "Volume dry-up plus cross-sectional price weakness. High score = quiet "
        "tape after a drawdown, the classic exhaustion/reversal setup."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the standardised volume dry-up reversal score."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    rel_vol = safe_div(volume, ts_mean(volume, 30))
    dryup = 0.5 - ts_rank(rel_vol, 60)

    ret_20 = safe_div(close, close.shift(20)) - 1.0
    weakness = 0.5 - rank(ret_20)

    return zscore(dryup + weakness)